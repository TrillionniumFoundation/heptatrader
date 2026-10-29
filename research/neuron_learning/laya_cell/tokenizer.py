"""Load the pinned tokenizer without cloning a newly owned native backend.

The complete AutoTokenizer/from_pretrained configuration path still runs. This
is a narrow adapter for the pinned Transformers implementation, not a cache or
a replacement tokenizer constructor. Call after the original snapshot check.
"""
import json
from pathlib import Path


class _OwnedBackend:
    """Transfer this call's private backend through the pinned copy protocol."""
    __slots__ = ('backend',)

    def __init__(self, backend):
        self.backend = backend

    def __deepcopy__(self, memo):
        return self.backend


def load_local_tokenizer(directory: Path):
    import transformers

    directory = Path(directory)
    options = dict(local_files_only=True, trust_remote_code=False, use_fast=True)

    def original():
        return transformers.AutoTokenizer.from_pretrained(str(directory), **options)

    if getattr(transformers, '__version__', None) != '5.20.2':
        return original()
    import tokenizers
    if tokenizers.__version__ != '0.23.1':
        return original()
    config_path = directory / 'tokenizer_config.json'
    if not config_path.is_file() or config_path.stat().st_size > 1024**2:
        return original()
    config = json.loads(config_path.read_text(encoding='utf-8'))
    selectors = {'auto_map', 'fast_tokenizer_files', 'tokenizer_file',
                 'tokenizer_object', 'vocab_file', 'merges_file', 'gguf_file',
                 'vocab', 'merges'}
    if (not isinstance(config, dict)
            or config.get('tokenizer_class') != 'PreTrainedTokenizerFast'
            or selectors.intersection(config)
            or not (directory / 'tokenizer.json').is_file()
            or any((directory / name).exists() for name in
                   ('config.json', 'tokenizer.model', 'vocab.json', 'vocab.txt', 'merges.txt'))):
        return original()

    from transformers.tokenization_utils_tokenizers import TokenizersBackend
    backend = tokenizers.Tokenizer.from_file(str(directory / 'tokenizer.json'))
    # The explicit empty path prevents convert_to_native_format from replacing
    # this owned object. _from_pretrained still uses its resolved real path for
    # all legacy AddedToken/configuration merging; do not supply a decoder here.
    tokenizer = transformers.AutoTokenizer.from_pretrained(
        str(directory), tokenizer_file='', tokenizer_object=_OwnedBackend(backend),
        **options)
    if type(tokenizer) is not TokenizersBackend or tokenizer.backend_tokenizer is not backend:
        # An AutoConfig-selected specialized class must keep the original path.
        return original()
    return tokenizer
