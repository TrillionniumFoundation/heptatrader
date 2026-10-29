"""Control boundaries for the owned backend; native parity is checked separately."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from laya_cell.tokenizer import load_local_tokenizer


class Backend:
    def __init__(self):
        self.vocab = {'original': 0}

    def __deepcopy__(self, memo):
        raise AssertionError('the fresh native backend must not be cloned')


class TokenizersBackend:
    def __init__(self, backend):
        self.backend_tokenizer = backend


class LocalTokenizerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / 'tokenizer_config.json'
        self.config.write_text(json.dumps({'tokenizer_class': 'PreTrainedTokenizerFast',
                                          'model_max_length': 8192, 'pad_token': '<pad>'}))
        (self.root / 'tokenizer.json').write_text('{}')
        self.original = object()
        self.return_type = TokenizersBackend
        self.return_foreign_backend = False

        def from_pretrained(path, **kwargs):
            if 'tokenizer_object' not in kwargs:
                return self.original
            # Exercise only the ownership protocol here. The real library's
            # complete config/token behavior is not simulated by this fixture.
            backend = copy.deepcopy(kwargs['tokenizer_object'])
            if self.return_foreign_backend:
                backend = Backend()
            return self.return_type(backend)

        self.load = Mock(side_effect=from_pretrained)
        self.native = Mock(side_effect=lambda path: Backend())
        self.transformers = ModuleType('transformers')
        self.transformers.__version__ = '5.20.2'
        self.transformers.AutoTokenizer = type('AutoTokenizer', (), {'from_pretrained': self.load})
        self.tokenizers = ModuleType('tokenizers')
        self.tokenizers.__version__ = '0.23.1'
        self.tokenizers.Tokenizer = type('Tokenizer', (), {'from_file': self.native})
        implementation = ModuleType('transformers.tokenization_utils_tokenizers')
        implementation.TokenizersBackend = TokenizersBackend
        modules = patch.dict(sys.modules, {'transformers': self.transformers,
            'tokenizers': self.tokenizers,
            'transformers.tokenization_utils_tokenizers': implementation})
        modules.start()
        self.addCleanup(modules.stop)

    def assert_original(self):
        self.assertIs(load_local_tokenizer(self.root), self.original)
        self.load.assert_called_once_with(str(self.root), local_files_only=True,
                                          trust_remote_code=False, use_fast=True)
        self.native.assert_not_called()
        self.load.reset_mock()

    def test_complete_auto_call_keeps_legacy_inputs_and_owns_each_backend(self):
        for name in ('special_tokens_map.json', 'added_tokens.json'):
            (self.root / name).write_text('{}')
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        deepcopy = copy.deepcopy
        first, second = load_local_tokenizer(self.root), load_local_tokenizer(self.root)
        self.assertEqual(self.native.call_count, 2)
        self.native.assert_called_with(str(self.root / 'tokenizer.json'))
        self.assertEqual(self.load.call_count, 2)
        for call in self.load.call_args_list:
            self.assertEqual(call.args, (str(self.root),))
            self.assertEqual(set(call.kwargs), {'tokenizer_file', 'tokenizer_object',
                                               'local_files_only', 'trust_remote_code', 'use_fast'})
            self.assertEqual(call.kwargs['tokenizer_file'], '')
            self.assertIs(call.kwargs['local_files_only'], True)
            self.assertIs(call.kwargs['trust_remote_code'], False)
            self.assertIs(call.kwargs['use_fast'], True)
        self.assertIsNot(first.backend_tokenizer, second.backend_tokenizer)
        first.backend_tokenizer.vocab['private'] = 1
        self.assertEqual(second.backend_tokenizer.vocab, {'original': 0})
        self.assertIs(copy.deepcopy, deepcopy)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir()})

    def test_other_dependency_versions_use_original_constructor(self):
        for module, version in ((self.transformers, '5.20.1'), (self.tokenizers, '0.23.0')):
            with self.subTest(module=module.__name__), patch.object(module, '__version__', version):
                self.assert_original()

    def test_custom_and_versioned_file_selectors_use_original_constructor(self):
        config = json.loads(self.config.read_text())
        for key in ('auto_map', 'fast_tokenizer_files', 'tokenizer_file', 'tokenizer_object',
                    'vocab_file', 'merges_file', 'gguf_file', 'vocab', 'merges'):
            with self.subTest(selector=key):
                self.config.write_text(json.dumps({**config, key: None}))
                self.assert_original()

    def test_model_config_and_alternate_vocabulary_files_use_original_constructor(self):
        for name in ('config.json', 'tokenizer.model', 'vocab.json', 'vocab.txt', 'merges.txt'):
            with self.subTest(file=name):
                path = self.root / name
                path.write_text('{}')
                self.assert_original()
                path.unlink()

    def test_missing_large_or_specialized_configuration_uses_original_constructor(self):
        original = self.config.read_bytes()
        for raw in (None, b' ' * (1024**2 + 1), b'[]', b'{"tokenizer_class":"BertTokenizer"}'):
            with self.subTest(configuration=repr(raw[:60]) if raw is not None else None):
                if raw is None:
                    self.config.unlink()
                else:
                    self.config.write_bytes(raw)
                self.assert_original()
        self.config.write_bytes(original)
        (self.root / 'tokenizer.json').unlink()
        self.assert_original()

    def test_specialized_dispatch_or_foreign_backend_falls_back(self):
        class Specialized(TokenizersBackend):
            pass

        for specialized in (True, False):
            with self.subTest(specialized=specialized):
                self.return_type = Specialized if specialized else TokenizersBackend
                self.return_foreign_backend = not specialized
                self.assertIs(load_local_tokenizer(self.root), self.original)
                self.assertEqual(self.load.call_count, 2)
                self.assertIn('tokenizer_object', self.load.call_args_list[0].kwargs)
                self.assertEqual(self.load.call_args_list[1].kwargs,
                                 {'local_files_only': True, 'trust_remote_code': False, 'use_fast': True})
                self.load.reset_mock()

    def test_supported_constructor_error_is_not_hidden_by_retry(self):
        self.load.side_effect = RuntimeError('fixed dependency rejected the owned backend')
        with self.assertRaisesRegex(RuntimeError, 'fixed dependency rejected'):
            load_local_tokenizer(self.root)
        self.assertEqual(self.load.call_count, 1)
        self.assertEqual(self.native.call_count, 1)


if __name__ == '__main__':
    unittest.main()
