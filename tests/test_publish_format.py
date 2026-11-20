"""The format 2 publication parts the publish tool builds: thumbnails, image uploads, class bodies, certification and pins."""
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import publish_catalogue as publisher  # noqa: E402


def png(width, height, mode="RGB"):
    out = io.BytesIO()
    Image.new(mode, (width, height), (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


class Thumbnails(unittest.TestCase):
    def test_a_wide_image_becomes_480_pixels_wide_webp(self):
        small = publisher.thumbnail(png(1280, 800))
        with Image.open(io.BytesIO(small)) as image:
            self.assertEqual((image.format, image.size), ("WEBP", (480, 300)))

    def test_a_narrow_image_keeps_its_width_and_transparency(self):
        small = publisher.thumbnail(png(300, 600, "RGBA"))
        with Image.open(io.BytesIO(small)) as image:
            self.assertEqual((image.format, image.size), ("WEBP", (300, 600)))
            self.assertIn("A", image.getbands())

    def test_the_same_image_gives_the_same_thumbnail(self):
        data = png(1000, 700)
        self.assertEqual(publisher.thumbnail(data), publisher.thumbnail(data))


class ImageUploads(unittest.TestCase):
    def test_each_image_is_sent_with_its_thumbnail_and_listed_by_digest(self):
        data = png(1280, 800)
        manifest = {"images": [{"path": "images/table.png", "caption": "The table", "platform": "web", "width": 1280, "height": 800}]}
        entries, uploads = publisher.images_of(manifest, {"images/table.png": data})
        main, thumb = uploads
        self.assertEqual(main, (hashlib.sha256(data).hexdigest(), data, "image/png"))
        self.assertEqual(thumb[2], "image/webp")
        self.assertEqual(thumb[0], hashlib.sha256(thumb[1]).hexdigest())
        self.assertEqual(entries, [{"digest": "sha256:" + main[0], "thumbnail": "sha256:" + thumb[0], "caption": "The table",
                                    "platform": "web", "width": 1280, "height": 800}])

    def test_a_package_without_images_sends_none(self):
        self.assertEqual(publisher.images_of({"images": []}, {}), ([], []))


class Pins(unittest.TestCase):
    def test_slots_and_job_candidates_are_pins(self):
        recipe = {"components": [{"id": "theme", "preferred": {"name": "a/theme", "version": "1.0.0"}},
                                 {"id": "host", "preferred": {"name": "a/host", "version": "2.0.0"}}],
                  "jobs": [{"id": "hosting", "slot": "host", "candidates": [{"component": "a/host"}, {"component": "a/other-host", "version": "3.1.0"}]}]}
        self.assertEqual(publisher.pinned_components(recipe), {"a/theme": "1.0.0", "a/host": "2.0.0", "a/other-host": "3.1.0"})


class Classes(unittest.TestCase):
    def test_each_carried_class_descriptor_is_a_format_2_body(self):
        descriptor = {"format": 2, "name": "a/theme-class", "version": "1.0.0"}
        self.assertEqual(publisher.class_bodies({"classes": [{"reference": {}, "descriptor": descriptor}]}), [{"format": 2, "class": descriptor}])


class Certified(unittest.TestCase):
    def test_only_the_level_n_guardian_key_is_certified(self):
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / "other.pem"
            subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(key)], check=True, capture_output=True)
            self.assertFalse(publisher.certified(key))
        level_n = Path.home() / ".config/ourapps/keys/guardian.pem"
        if level_n.is_file():
            self.assertTrue(publisher.certified(level_n))


if __name__ == "__main__":
    unittest.main()
