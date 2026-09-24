"""Local, pinned CPU embeddings. Retrieval scores are never approval decisions."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from collections import OrderedDict

from risk_audit.util import norm_text, sha256_file


class SemanticUnavailable(RuntimeError):
    pass


class LocalEncoder:
    def __init__(self, directory, cache_path, *, threads=2, expected_manifest=None):
        start=time.perf_counter()
        directory=Path(directory)
        if not directory.is_dir(): raise SemanticUnavailable('本地语义模型不存在；未联网下载，未执行语义辅助')
        manifest_path=directory/'manifest.json'
        if not manifest_path.exists(): raise SemanticUnavailable('本地模型缺少校验清单')
        if expected_manifest and sha256_file(manifest_path)!=expected_manifest:
            raise SemanticUnavailable('本地模型校验清单与规则包不一致')
        self.manifest=json.loads(manifest_path.read_text())
        if (self.manifest.get('model_id')!='BAAI/bge-small-zh-v1.5' or
            self.manifest.get('pooling')!='CLS_L2' or self.manifest.get('dimension')!=512):
            raise SemanticUnavailable('不支持的本地模型配置')
        for name in ('model.onnx','tokenizer.json'):
            spec=self.manifest.get('files',{}).get(name,{})
            if not (directory/name).is_file() or sha256_file(directory/name)!=spec.get('sha256'):
                raise SemanticUnavailable(f'本地模型资源校验失败：{name}')
        try:
            os.environ.setdefault('TOKENIZERS_PARALLELISM','false')
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc: raise SemanticUnavailable('未安装本地语义运行依赖') from exc
        self.np=np
        options=ort.SessionOptions();options.intra_op_num_threads=threads;options.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(directory/'model.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
        self.tokenizer=Tokenizer.from_file(str(directory/'tokenizer.json'))
        self.tokenizer.no_truncation();self.tokenizer.no_padding()
        self.fingerprint=sha256_file(manifest_path)
        cache_path=Path(cache_path);cache_path.parent.mkdir(parents=True,exist_ok=True)
        self.cache=sqlite3.connect(cache_path)
        self.cache.execute('CREATE TABLE IF NOT EXISTS embeddings (model TEXT, text TEXT, vector BLOB, digest TEXT, PRIMARY KEY(model,text))')
        self.memory=OrderedDict()
        self.stats={'model_id':self.manifest['model_id'],'revision':self.manifest['revision'],
                    'provider':self.session.get_providers(),'threads':threads,'cold_start_seconds':time.perf_counter()-start,
                    'requests':0,'encoded_texts':0,'cache_hits':0,'memory_hits':0,'memory_capacity':4096,'encoding_seconds':0.0}

    def _remember(self,text,vector):
        self.memory[text]=vector;self.memory.move_to_end(text)
        while len(self.memory)>4096:self.memory.popitem(last=False)

    def encode(self,texts,batch_size=16):
        if type(batch_size) is not int or not 1<=batch_size<=16:
            raise ValueError('语义编码每批必须为1至16项')
        np=self.np;texts=[norm_text(x) for x in texts];unique=list(dict.fromkeys(texts));vectors={};pending=[]
        self.stats['requests']+=len(texts)
        for text in unique:
            if text in self.memory:
                vectors[text]=self.memory[text];self.memory.move_to_end(text)
                self.stats['memory_hits']+=1;continue
            hit=self.cache.execute('SELECT vector,digest FROM embeddings WHERE model=? AND text=?',(self.fingerprint,text)).fetchone()
            if hit and hashlib.sha256(hit[0]).hexdigest()==hit[1]:
                vector=np.frombuffer(hit[0],dtype='<f4').copy()
                if vector.shape==(512,) and np.isfinite(vector).all() and abs(float(np.linalg.norm(vector))-1)<1e-3:
                    vectors[text]=vector;self._remember(text,vector);self.stats['cache_hits']+=1;continue
            pending.append(text)
        start=time.perf_counter()
        for offset in range(0,len(pending),batch_size):
            batch=pending[offset:offset+batch_size]
            encoded=self.tokenizer.encode_batch(batch)
            if any(len(e.ids)>self.manifest['max_tokens'] for e in encoded):
                raise SemanticUnavailable('原文超过语义模型长度上限，未截断后判定')
            width=max(len(e.ids) for e in encoded)
            feed={key:np.zeros((len(batch),width),dtype=np.int64) for key in ('input_ids','attention_mask','token_type_ids')}
            for i,e in enumerate(encoded):
                feed['input_ids'][i,:len(e.ids)]=e.ids
                feed['attention_mask'][i,:len(e.ids)]=1
                feed['token_type_ids'][i,:len(e.ids)]=e.type_ids
            values=self.session.run(['embeddings'],feed)[0]
            for text,vector in zip(batch,values):
                # Own this row's storage so a surviving LRU entry cannot retain
                # an entire old inference batch.
                vector=np.asarray(vector,dtype='<f4').copy()
                if vector.shape!=(512,) or not np.isfinite(vector).all() or abs(float(np.linalg.norm(vector))-1)>1e-3:
                    raise SemanticUnavailable('语义模型输出无效')
                raw=vector.tobytes();vectors[text]=vector
                self._remember(text,vector)
                self.cache.execute('INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?)',
                                   (self.fingerprint,text,raw,hashlib.sha256(raw).hexdigest()))
            self.stats['encoded_texts']+=len(batch)
        self.cache.commit();self.stats['encoding_seconds']+=time.perf_counter()-start
        return np.stack([vectors[t] for t in texts]) if texts else np.empty((0,512),dtype=np.float32)

    def rank(self,query,entries,top_k=3):
        if not entries:return []
        # Embeddings are normalized; dot product is cosine similarity.
        values=self.encode([query,*[e['text'] for e in entries]])
        scores=values[1:] @ values[0]
        order=sorted(range(len(entries)),key=lambda i:(-float(scores[i]),entries[i]['text']))[:top_k]
        return [{**entries[i],'score':round(float(scores[i]),6),'automatic_equivalence':False} for i in order]

    def close(self):self.cache.close()


class SemanticAssistant:
    def __init__(self,config,runtime):
        self.config=config;self.runtime=runtime;self.encoder=None;self.error=None;self.events={}

    def suggest(self,text,entries,*,domain,scope=()):
        key=(domain,tuple(scope),norm_text(text),tuple((e['text'],e.get('concept','')) for e in entries))
        if key in self.events:return self.events[key]
        result={'domain':domain,'scope':list(scope),'text':text,'candidates':[],'decision':'candidate_only'}
        if not self.config.get('enabled'):
            result['status']='disabled';return result
        if self.config.get('version',1)>=2 and domain not in self.config['allowed_domains']:
            result.update(status='excluded',reason='该领域不在本期语义辅助范围');self.events[key]=result;return result
        try:
            if self.error:raise SemanticUnavailable(self.error)
            if self.encoder is None:
                self.encoder=LocalEncoder(self.runtime['model_dir'],self.runtime['cache_path'],
                                          threads=self.config['threads'],expected_manifest=self.config['model_manifest_sha256'])
            result['candidates']=self.encoder.rank(text,entries,self.config.get('top_k',3))
            result['status']='ranked'
            if self.config.get('version',1)>=2 and domain=='applicability':
                from risk_audit.semantic_matching import filter_applicability
                filter_applicability(result,self.config['retrieval'])
        except (SemanticUnavailable, OSError, ValueError) as exc:
            result.update(status='unavailable',reason=str(exc))
            if self.encoder is None:self.error=str(exc)
        self.events[key]=result
        return result

    def suggest_responsibility(self,text,entries,*,scope,unit_names,generic_units=()):
        from risk_audit.semantic_matching import responsibility_candidates
        from risk_audit.util import sha256_json
        key=('structured_responsibility',sha256_json([text,entries,scope,sorted(unit_names),sorted(generic_units)]))
        if key in self.events:return self.events[key]
        result=responsibility_candidates(self,text,entries,scope=scope,unit_names=unit_names,generic_units=generic_units)
        self.events[key]=result
        return result

    def report(self):
        states={state:sum(e['status']==state for e in self.events.values()) for state in sorted({'ranked','unavailable',*[e['status'] for e in self.events.values()]})}
        return {'enabled':bool(self.config.get('enabled')),'stats':self.encoder.stats if self.encoder else {},
                'error':self.error,'events':list(self.events.values()),'scores_are_probabilities':False,
                'unverified_candidates_can_pass':False,'runtime_network_calls':False,'event_status_counts':states,
                'usage_status':'disabled' if not self.config.get('enabled') else 'not_needed' if not self.events else
                               'partial' if states['unavailable'] else 'completed_candidates'}


_SERVICES={}
def get_assistant(resources):
    config=resources.get('semantic_config',{});runtime=resources.get('_semantic_runtime',{})
    if not config or not runtime:return None
    key=json.dumps([config,runtime],sort_keys=True)
    if key not in _SERVICES:_SERVICES[key]=SemanticAssistant(config,runtime)
    return _SERVICES[key]


def close_assistant(resources):
    key=json.dumps([resources.get('semantic_config',{}),resources.get('_semantic_runtime',{})],sort_keys=True)
    service=_SERVICES.pop(key,None)
    if service and service.encoder:service.encoder.close()
