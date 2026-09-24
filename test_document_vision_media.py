"""Image payload encoding must match bytes; no live API calls."""
import base64
from types import SimpleNamespace as NS
import unittest
from document_interpreter import Budget
from document_vision import VisionReader


class ImageMediaTests(unittest.TestCase):
    def check_media(self, data, expected):
        sent = []
        def count_tokens(**kwargs):
            sent.append(kwargs['messages'][0]['content'][0]['source'])
            return NS(input_tokens=100)
        def create(**kwargs):
            sent.append(kwargs['messages'][0]['content'][0]['source'])
            return NS(content=[NS(type='text', text='{"rows": [], "grand_total": null}')],
                      stop_reason='end_turn', usage=NS(input_tokens=100, output_tokens=20))
        client = NS(messages=NS(count_tokens=count_tokens, create=create))
        result = VisionReader(client=client).read_page(data, Budget(1))
        self.assertGreater(result['usd'], 0)
        for payload in sent:
            self.assertEqual(payload['media_type'], expected)
            self.assertEqual(base64.b64decode(payload['data']), data)

    def test_jpeg_bytes_are_sent_as_jpeg_to_count_and_create(self):
        self.check_media(b'\xff\xd8\xff\xe0synthetic-jpeg', 'image/jpeg')

    def test_png_keeps_png_media_type(self):
        self.check_media(b'\x89PNG\r\n\x1a\nsynthetic-png', 'image/png')


if __name__ == '__main__':
    unittest.main()
