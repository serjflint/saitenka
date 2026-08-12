from saitenka_dict.media import image_size, normalize_glossary


def test_gif_size_and_preferred_dimensions_are_separate():
    gif = b"GIF89a" + (350).to_bytes(2, "little") + (200).to_bytes(2, "little")

    content = normalize_glossary(
        {"tag": "img", "path": "image.gif", "width": 35, "height": 20},
        {"image.gif": gif},
    )

    assert image_size(gif) == (350, 200)
    assert content == {
        "tag": "img",
        "path": "image.gif",
        "width": 350,
        "height": 200,
        "preferredWidth": 35,
        "preferredHeight": 20,
    }
