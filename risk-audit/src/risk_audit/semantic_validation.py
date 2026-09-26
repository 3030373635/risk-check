"""Validate explicit lexicon semantics separately from recognition suggestions."""
import re


def validate_semantic_config(cfg):
    base={'version','enabled','model_id','model_manifest_sha256','threads','top_k','candidate_only'}
    if not isinstance(cfg,dict):return ['semantic_config: object required']
    required=base|({'allowed_domains','retrieval'} if cfg.get('version')==2 else set())
    if set(cfg)!=required:return ['semantic_config: incomplete or unknown fields']
    if (type(cfg['version']) is not int or cfg['version'] not in (1,2) or type(cfg['enabled']) is not bool or
        cfg['model_id']!='BAAI/bge-small-zh-v1.5' or cfg['candidate_only'] is not True or
        type(cfg['threads']) is not int or not 1<=cfg['threads']<=4 or
        type(cfg['top_k']) is not int or not 1<=cfg['top_k']<=5 or
        not re.fullmatch('[a-f0-9]{64}',str(cfg['model_manifest_sha256']))):
        return ['semantic_config: invalid local-only candidate configuration']
    if cfg['version']==2:
        if cfg['allowed_domains']!=['applicability','responsibility']:
            return ['semantic_config: only current applicability/responsibility candidate domains allowed']
        policy=cfg['retrieval'];fields={'min_position_score','min_department_score','min_applicability_score','ambiguity_margin'}
        if (not isinstance(policy,dict) or set(policy)!=fields or
            not all(type(v) in (int,float) and 0<v<1 for v in policy.values())):
            return ['semantic_config.retrieval: bounded candidate filters required; not approval thresholds']
    return []


def validate_lexicon(lexicon):
    errors=[]
    if not isinstance(lexicon,dict) or set(lexicon)!={'version','applicability','prototypes','relations'} or lexicon.get('version')!=1:
        return ['semantic_lexicon: unsupported structure']
    states={'explicit_positive':'applicable','explicit_negative':'not_applicable',
            'business_absent':None,'template_adopted':None,'template_modified':None,'scope_explanation':None}
    for section in ('applicability','prototypes','relations'):
        if not isinstance(lexicon[section],list):errors.append(f'semantic_lexicon.{section}: array required');continue
        for item in lexicon[section]:
            if not isinstance(item,dict):errors.append(f'semantic_lexicon.{section}: object required');continue
            if section=='applicability':
                keys={'id','pattern','state','decision','reason_from_text','source'}
                if set(item)!=keys or item.get('state') not in states or states.get(item.get('state'))!=item.get('decision'):
                    errors.append('semantic_lexicon.applicability: invalid state/decision');continue
                try:re.compile(item['pattern'])
                except (re.error,TypeError):errors.append('semantic_lexicon.applicability: invalid pattern')
                if not item['source'] or type(item['reason_from_text']) is not bool:errors.append('semantic_lexicon.applicability: missing evidence')
            elif section=='prototypes':
                if set(item)!={'text','concept','source'} or not all(isinstance(v,str) and v.strip() for v in item.values()):errors.append('semantic_lexicon.prototypes: incomplete prototype')
            else:
                required={'source_text','target_text','field','relation','scope','evidence','status','counterexamples'}
                if set(item)!=required or item.get('status') not in {'recognition_only','candidate_only'}:
                    errors.append('semantic_lexicon.relations: unverified relations cannot become automatic aliases')
    return errors
