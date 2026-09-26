"""Build-time only: fetch a pinned public encoder and export local CPU ONNX.

The auditor never imports this module or downloads resources at runtime.
"""
from pathlib import Path
import argparse
import hashlib
import json
import urllib.request

REPO = 'BAAI/bge-small-zh-v1.5'
REVISION = '7999e1d3359715c523056ef9478215996d62a620'
FILES = ['model.safetensors', 'config.json', 'tokenizer.json', 'tokenizer_config.json',
         'special_tokens_map.json', 'vocab.txt', '1_Pooling/config.json', 'README.md']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.source.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        path = args.source / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            url = f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}'
            temp = path.with_suffix(path.suffix + '.download')
            with urllib.request.urlopen(url, timeout=120) as response, temp.open('wb') as out:
                while block := response.read(1024 * 1024): out.write(block)
            temp.replace(path)
        print(f'Ready: {name} ({path.stat().st_size} bytes)', flush=True)
    import shutil
    import numpy as np
    import onnxruntime as ort
    import torch
    from transformers import AutoModel, AutoTokenizer
    torch.set_num_threads(2)
    model = AutoModel.from_pretrained(args.source, local_files_only=True, use_safetensors=True).eval()
    tokenizer = AutoTokenizer.from_pretrained(args.source, local_files_only=True)

    class Encoder(torch.nn.Module):
        def __init__(self, base):
            super().__init__(); self.base = base
        def forward(self, input_ids, attention_mask, token_type_ids):
            out = self.base(input_ids=input_ids, attention_mask=attention_mask, token_type_ids=token_type_ids)
            return torch.nn.functional.normalize(out.last_hidden_state[:, 0], p=2, dim=1)

    encoder = Encoder(model).eval()
    sample = tokenizer(['暂无此业务，参照省公司', '对合同的准确性负有审核责任。'], padding=True, return_tensors='pt')
    args.output.mkdir(parents=True, exist_ok=True)
    dest = args.output / 'model.onnx'
    names = ['input_ids', 'attention_mask', 'token_type_ids']
    torch.onnx.export(encoder, tuple(sample[k] for k in names), str(dest),
                      input_names=names, output_names=['embeddings'],
                      dynamic_axes={**{k: {0:'batch', 1:'sequence'} for k in names}, 'embeddings': {0:'batch'}},
                      opset_version=17, dynamo=False)
    options = ort.SessionOptions(); options.intra_op_num_threads=2; options.inter_op_num_threads=1
    session=ort.InferenceSession(str(dest), sess_options=options, providers=['CPUExecutionProvider'])
    observed=session.run(None,{k:sample[k].numpy() for k in names})[0]
    with torch.no_grad(): expected=encoder(*(sample[k] for k in names)).numpy()
    error=float(np.max(np.abs(observed-expected)))
    if error>1e-5: raise RuntimeError(f'ONNX differs from source encoder: {error}')
    shutil.copy2(args.source/'tokenizer.json', args.output/'tokenizer.json')
    shutil.copy2(args.source/'README.md',args.output/'UPSTREAM_README.md')
    hash_file=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    manifest={'schema_version':1,'model_id':REPO,'revision':REVISION,'license':'MIT',
              'dimension':512,'max_tokens':512,'pooling':'CLS_L2','precision':'float32',
              'source_weights_sha256':hash_file(args.source/'model.safetensors'),
              'source_comparison_max_abs_error':error,
              'files':{n:{'sha256':hash_file(args.output/n),'bytes':(args.output/n).stat().st_size}
                       for n in ['model.onnx','tokenizer.json']}}
    (args.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps(manifest,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
