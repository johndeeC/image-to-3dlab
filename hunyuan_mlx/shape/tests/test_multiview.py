"""Hunyuan3D-2mv's several-view input: the view tags and the encoder, at a tiny size (no weights)."""
import mlx.core as mx
import numpy as np
import pytest

from hy3dmlx.models.dinov2 import DinoImageEncoderMV, sincos_1d


def test_view_tags_match_the_reference_formula():
    tags = sincos_1d(8, np.arange(4))
    omega = 1.0 / 10000 ** (np.arange(4) / 4.0)
    assert tags.shape == (4, 8)
    np.testing.assert_allclose(tags[2], np.concatenate([np.sin(2 * omega), np.cos(2 * omega)]))
    np.testing.assert_allclose(tags[0], [0, 0, 0, 0, 1, 1, 1, 1])     # the front: sin 0, cos 0


def _tiny():
    # 28x28 pictures at patch 14: 4 patches + the class token = 5 tokens per view
    enc = DinoImageEncoderMV(hidden=8, heads=2, layers=1, ffn_dim=16, swiglu=True, patch=14, num_tokens=5)
    mx.eval(enc.parameters())
    return enc


def test_each_view_is_tagged_by_its_index_and_all_are_joined():
    enc = _tiny()
    pixels = mx.zeros((2, 28, 28, 3))
    plain = enc.model(pixels)                                     # [2, 5, 8], no tags
    joined = enc.encode_views(pixels, [0, 2])                     # front and back
    assert joined.shape == (1, 10, 8)
    tags = sincos_1d(8, np.arange(4))
    np.testing.assert_allclose(np.array(joined[0, 5:]), np.array(plain[1]) + tags[2], rtol=1e-5, atol=1e-5)
    assert enc.unconditional_embedding(1, views=2).shape == (1, 10, 8)


def test_the_tags_are_not_weights_to_load():
    from mlx.utils import tree_flatten
    names = [k for k, _ in tree_flatten(_tiny().parameters())]
    assert not any("view" in n for n in names)


def test_a_one_picture_model_says_it_cannot_take_views():
    from hy3dmlx.pipeline import Hunyuan3DShapePipeline

    class OnePicture:
        pass
    pipe = Hunyuan3DShapePipeline(OnePicture(), None, None, {})
    with pytest.raises(ValueError, match="several views need Hunyuan3D-2mv"):
        pipe.encode_views({"front": "a.png"})
