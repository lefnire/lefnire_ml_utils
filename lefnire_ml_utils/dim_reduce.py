# https://github.com/tensorflow/tensorflow/issues/2117
import tensorflow as tf
config = tf.compat.v1.ConfigProto()
config.gpu_options.allow_growth = True
session = tf.compat.v1.Session(config=config)

import math, os, pdb, gc
import numpy as np
import torch
from tensorflow.keras import backend as K
from tensorflow.keras.layers import Layer, Input, Dense, Lambda, Concatenate, BatchNormalization
from tensorflow.keras.models import Model, load_model
from tensorflow.keras.callbacks import EarlyStopping
from tensorflow.keras.optimizers import Adam
from tqdm import tqdm

def autoencode_(x, dims, save_load_path, preserve_cosine, batch_norm):
    ###
    # Run the model
    if save_load_path and os.path.exists(save_load_path):
        encoder = load_model(save_load_path)
        return encoder.predict(x)

    ###
    # Compile model

    # activation intuitions:
    # linear + mse: no bounds, let DNN learn what it learns - simplest
    # tanh/sigmoid + (mse?)/binary_cross_entropy: constrain output to [-1 1] / [0 1]. Esp. useful if using output in
    #   downstream tasks like cosine() or DNN which want normalized outputs.
    # elu + mse: seems to perform best, but I don't have intuition

    # tanh best here to enforce normalized outputs for downstream cosine(), so we can skip that step.
    encode_act = 'tanh'  # linear
    # tanh best here since we're preserving cosine [-1 1] below. Though should this match cosine(abs=BOOL) for
    # downstream task?
    dist_act = ('tanh', 'mse')  # linear, mse
    act = 'elu'  # relu

    # reuse layer, since x/x_other are same
    bn = BatchNormalization() if batch_norm else None

    input_dim = x.shape[1]
    x_input = Input(shape=(input_dim,), name='x_input')
    encoder = bn(x_input) if batch_norm else x_input
    for i, d in enumerate(dims):
        first_, last_ = i == 0, i == len(dims) - 1
        encoder = Dense(d, activation=encode_act if last_ else act)(encoder)

    if preserve_cosine:
        x_other_input = Input(shape=(input_dim,), name='x_other_input')
        merged = Concatenate(1)([
            encoder,
            bn(x_other_input) if batch_norm else x_other_input
        ])
    else:
        merged = encoder

    decoder = merged
    for d in dims[::-1][1:]:
        decoder = Dense(d, activation=act)(decoder)
    decoder = Dense(input_dim, activation='linear', name='decoder_out')(decoder)

    d_in = [x_input]
    d_out, e_out = [decoder], [encoder]
    if preserve_cosine:
        dist_out = Dense(1, activation=dist_act[0], name='dist_out')(decoder)
        d_in.append(x_other_input)
        d_out.append(dist_out)
    decoder = Model(d_in, d_out)
    encoder = Model(x_input, e_out)

    loss, loss_weights = {'decoder_out': 'mse'}, {'decoder_out': 1.}
    if preserve_cosine:
        loss['dist_out'] = dist_act[1]
        loss_weights['dist_out'] = 1.
    decoder.compile(
        metrics=loss,
        loss=loss,
        loss_weights=loss_weights,
        optimizer=Adam(learning_rate=.0001),
    )
    decoder.summary()

    ###
    # Train model
    # np.random.shuffle(x)  # shuffle all data first, since validation_split happens before shuffle

    shuffle = np.arange(x.shape[0])
    np.random.shuffle(shuffle)

    if preserve_cosine:
        print("AE: calc pairwise_distances_chunked")
        x_t = torch.tensor(x)
        x_t = x_t / x_t.norm(dim=1)[:, None]
        dists = np.concatenate([
            torch.mm(x_t[i:i + 1], x_t[j:j + 1].T).cpu()
            for i, j in zip(np.arange(x.shape[0]), shuffle)
        ])

    # https://wizardforcel.gitbooks.io/deep-learning-keras-tensorflow/content/8.2%20Multi-Modal%20Networks.html
    inputs = {'x_input': x}
    outputs = {'decoder_out': x}
    if preserve_cosine:
        inputs['x_other_input'] = x[shuffle]
        outputs['dist_out'] = dists

    es = EarlyStopping(monitor='val_loss', mode='min', patience=3, min_delta=.0001)
    decoder.fit(
        inputs,
        outputs,
        epochs=50,
        batch_size=128,
        shuffle=True,
        callbacks=[es],
        validation_split=.3,
    )
    encoder.save(save_load_path)
    return encoder.predict(x)

def autoencode(
    x,
    dims=[500, 150, 20],
    save_load_path=None,
    preserve_cosine=True,
    batch_norm=False
):
    """
    Auto-encode X from input_dim to latent.
    :param x: embeddings to encode. Don't need to pre-normalize, see batch_norm
    :param dims: ae architecture, with last value being latent dim
    :param save_load_path: if provided, will attempt to load this model for use. If not exists, will train the
        model & save here, for use next time
    :param preserve_cosine: If true, AE will try to preserve pairwise cosine distance (x<->x). Just a hair-brained
        idea, I'm not a researcher; my thinking is AE might change manifold and ruin cosine-ability
    :param batch_norm: Whether to batch-normalize the input. It's a learned layer, so you'd be able to then use this
        trained model later without needing to normalize future inputs. I'm trying with False, hoping the DNN itself
        learns normalization in the process. Note, the embedding layer (the AE output) itself is normalized, since it's
        tanh; so this has less to do with downstream use, and more about training theory.
    """

    # Wrap function call so all Keras models lose context for garbage-collection. It doesn't work, Keras and its
    # memory leaks... but hey, worth the try.
    preds = autoencode_(x, dims, save_load_path, preserve_cosine, batch_norm)
    gc.collect()
    K.clear_session()
    return preds
