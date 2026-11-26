import hashlib
import re
import unittest
from pathlib import Path


class LocalFontTests(unittest.TestCase):
    def test_css_references_the_four_exact_local_brand_faces(self):
        static = Path(__file__).resolve().parents[1] / 'website/static'
        css = (static / 'style.css').read_text(encoding='utf-8')
        urls = re.findall(r'url\(["\']([^"\']+)["\']\)', css)
        expected = {
            'fonts/EurostileTBla.woff': '1caf5a0828e2054b7f4c35280340e11a169ac7975657748342ee8540a0c93420',
            'fonts/EurostileT.woff': '188ae12d8aefb5d11ef35e835839c17c6d5dfb4633f2028dae5482836d712162',
            'fonts/bellgothic.woff': '664af985b1b6a3c263a695d334538dd9b7e6f0ae602b7558d9dc61c08e91f14f',
            'fonts/bellgothic-black.woff': '2cefe21bfe01a8817a871330c4ea1011de52c42c03fa3278beacc0894e34f2f3',
        }
        self.assertCountEqual(urls, expected)
        for url, digest in expected.items():
            raw = (static / url).read_bytes()
            self.assertEqual(raw[:4], b'wOFF')
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
        self.assertNotIn('font-weight: medium', css)
