from deepviz_webui import app, cached
from deepviz_webui.imagecorpus import CIFAR10ImageCorpus
from deepviz_webui.utils.decaf import load_from_convnet, reshape_layer_for_visualization, \
    get_layer_dimensions
from deepviz_webui.utils.images import normalize, generate_svg_filter_map

from decaf.util.visualize import show_multiple, show_channels, show_single

from flask import render_template, request, Response

from cStringIO import StringIO
from functools import wraps
from gpumodel import IGPUModel
from matplotlib import pyplot, cm
import networkx as nx
import numpy as np
from PIL import Image
from shownet import ShowConvNet
import os

_models = None
_model = None  # TODO: remove this once the graph is drawn from Decaf
_image_corpus = None


def get_image_corpus():
    global _image_corpus
    if _image_corpus is None:
        _image_corpus = CIFAR10ImageCorpus(app.config["CIFAR_10_PATH"])
    return _image_corpus


@app.route("/imagecorpus/<filename>")
def get_image_from_corpus(filename):
    corpus = get_image_corpus()
    image = corpus.get_image(filename)
    scale = int(request.args.get('scale', 1))
    if scale != 1:
        (width, height) = image.size
        image = image.resize((width * scale, height * scale), Image.NEAREST)
    png_buffer = StringIO()
    image.save(png_buffer, format="PNG")
    png = png_buffer.getvalue()
    png_buffer.close()
    return Response(png, mimetype="image/png")


@app.route("/imagecorpus/search/<query>")
def image_corpus_query(query):
    corpus = get_image_corpus()
    image_names = list(corpus.find_images(query))
    return Response("\n".join(image_names), mimetype="text/plain")



def get_models():
    global _models
    if _models is None:
        model_path = app.config["TRAINED_MODEL_PATH"]
        checkpoints = sorted(os.listdir(model_path))
        _models = [load_from_convnet(os.path.join(model_path, c)) for c in checkpoints]
    return _models



# TODO: remove this once the graph is drawn from Decaf:
def get_model():
    global _model
    if _model is None:
        # This code is adapted from gpumodel.py and shownet.py
        load_dic = IGPUModel.load_checkpoint(app.config["TRAINED_MODEL_PATH"])
        op = ShowConvNet.get_options_parser()
        old_op = load_dic["op"]
        old_op.merge_from(op)
        op = old_op
        _model = ShowConvNet(op, load_dic)
    return _model


def pylabToPNG(view_func):
    """
    Decorator for creating views that return pylab images as PNGs.
    Adds querystring options for performing scaling.
    """
    def _decorator(*args, **kwargs):
        image_data = view_func(*args, **kwargs)
        png_buffer = StringIO()
        pyplot.imsave(png_buffer, image_data, cmap=cm.gray, format='png')
        png_buffer.reset()
        image = Image.open(png_buffer)
        scale = int(request.args.get('scale', 1))
        if scale != 1:
            (width, height) = image.size
            image = image.resize((width * scale, height * scale), Image.NEAREST)
        png_buffer = StringIO()
        image.save(png_buffer, format="PNG")
        png = png_buffer.getvalue()
        png_buffer.close()
        return Response(png, mimetype="image/png")
    return wraps(view_func)(_decorator)


@app.route("/checkpoints/<int:checkpoint>/layers/<layername>/overview.png")
@pylabToPNG
def layer_overview_png(checkpoint, layername):
    model = get_models()[checkpoint]
    layer = model.layers[layername]
    (num_filters, ksize, num_channels) = get_layer_dimensions(layer)
    reshaped = reshape_layer_for_visualization(layer, combine_channels=(num_channels == 3))
    ncols = 1 if num_channels == 3 else num_channels
    return show_multiple(normalize(reshaped), ncols=ncols)


@app.route("/checkpoints/<int:checkpoint>/layers/<layername>/apply/<imagename>/overview.png")
@pylabToPNG
def convolved_layer_overview_png(checkpoint, imagename, layername):
    """
    Visualizes the applications of a layer's filters to an image.
    """
    # This is based on decaf's "imagenet" script:
    corpus = get_image_corpus()
    image = corpus.get_image(imagename + ".png")
    model = get_models()[checkpoint]
    arr = np.array(image.getdata()).reshape(1, 32, 32, 3).astype(np.float32)
    classified = model.predict(data=arr, output_blobs=[layername + "_cudanet_out"])
    layer = classified[layername + "_cudanet_out"]
    if layername.startswith("fc") and layername.endswith("_neuron"):
        # For fcN, the layer's shape is (1, N).
        return show_single(layer[0])
    else:
        layer = layer[0, :, :, :]  # shape this into a (k, k, num_filters) array
        return show_channels(layer)


@app.route("/layers/<layername>/overview.svg")
def layer_overview_svg_container(layername):
    model = get_models()[0]
    layer = model.layers[layername]
    (num_filters, ksize, num_channels) = get_layer_dimensions(layer)
    ncols = 1 if num_channels == 3 else num_channels
    scale = int(request.args.get('scale', 1))
    svg = generate_svg_filter_map(num_filters * ncols, ksize, ncols, scale)
    return Response(svg, mimetype="image/svg+xml")


@app.route("/layers.svg")
@cached()
def layer_dag_to_svg():
    model = get_model()
    graph = nx.DiGraph()
    for layer in model.layers:
        graph.add_node(layer['name'], layer_attributes=layer)
    for layer in model.layers:
        for inputLayer in layer.get("inputLayers", []):
            graph.add_edge(inputLayer['name'], layer['name'])
    pydot_graph = nx.to_pydot(graph)
    pydot_graph.set_rankdir("LR")
    svg = pydot_graph.create_svg(prog="dot")
    return Response(svg, mimetype="image/svg+xml")


@app.route("/")
def index():
    context = {
        'num_timesteps' : len(get_models()),
        'model' : get_models()[0],
    }
    return render_template('index.html', **context)
