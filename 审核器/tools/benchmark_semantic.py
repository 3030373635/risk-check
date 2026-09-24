"""Offline encoder benchmark on explicitly labelled expression families.

Labels are developer-authored examples, not independently annotated audit truth.
"""
from pathlib import Path
import argparse,json,time,resource,platform,socket
from risk_audit.configuration.loader import load_pack
from risk_audit.semantic import LocalEncoder,SemanticUnavailable
from risk_audit.applicability import interpret

DEVELOPMENT=[
 ('applicable','本条适用于本单位'),('applicable','这项控制要求在本公司执行'),
 ('not_applicable','本措施不适用于本单位'),('not_applicable','本公司无需执行这项控制措施'),
 ('business_absent','本单位目前没有开展该项业务'),('business_absent','目前没有发生这项业务'),
 ('template_adopted','直接引用省公司的矩阵模板'),('template_adopted','沿用省公司模板'),
 ('template_modified','修改了责任主体'),('template_modified','修改控制措施和控制载体'),
 ('scope_explanation','县公司无业务核算岗位'),('scope_explanation','本单位没有相应业务权限'),
 ('unknown','尚未确定是否适用'),('unknown','该条是否适用还需要确认'),
]
VALIDATION=[
 ('applicable','这条控制要求适用于我公司'),('applicable','我们公司需要执行本项控制'),
 ('applicable','该项规定在本单位执行'),
 ('not_applicable','我公司不属于本项控制的适用范围'),('not_applicable','这一控制要求不适用于我单位'),
 ('not_applicable','本单位无需落实这条控制措施'),
 ('business_absent','目前尚未发生此类业务'),('business_absent','本公司迄今没有办理过这类业务'),
 ('business_absent','本单位现阶段未开展相关业务活动'),
 ('template_adopted','此次填报采用省里原有的矩阵内容'),('template_adopted','照搬省公司版本，未作改动'),
 ('template_adopted','保留上级模板的原文'),
 ('template_modified','把原责任主体换成本公司岗位'),('template_modified','已对控制内容作本地调整'),
 ('template_modified','在省公司原稿上调整了控制载体'),
 ('scope_explanation','相关资金流水不在本公司的操作权限内'),('scope_explanation','我单位尚未配置对应系统'),
 ('scope_explanation','本单位没有设置业务核算岗位'),
 ('unknown','适用范围还有待确认'),('unknown','不能确定本项要求是否覆盖我公司'),
 ('unknown','这条规定能否用于本单位还没定下来'),
]
PAIRS=[
 ('对合同准确性负审核责任','承担合同准确性的审核责任',True,'责任表述改写'),
 ('负有审核责任','不负有审核责任',False,'否定'),
 ('负有审核责任','负有审批责任',False,'角色'),
 ('暂无此业务','本单位目前没有开展该项业务',True,'当前业务状态'),
 ('暂无此业务','本控制措施不适用',False,'业务状态与适用性'),
 ('直接引用','本措施适用',False,'模板采用与适用性'),
 ('凤凰分公司','古丈分公司',False,'主体'),
 ('财务部主任','综合管理室主任',False,'部门'),
 ('四级职员','五级职员',False,'职级'),
 ('张伟','张炜',False,'姓名'),
 ('设备（资产）管理-控制措施01','设备（资产）管理-控制措施02',False,'编号'),
 ('合同草案','已签订合同',False,'文档状态'),
 ('销项发票','进项发票',False,'载体限定'),
 ('付款申请单','收款申请单',False,'方向'),
 ('工资发放明细表','工资扣款明细表',False,'业务对象'),
 ('薪酬专责（四级职员）','四级职员',False,'缺少同部门同措施上下文时不能认定同岗位'),
]

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
 args.output.mkdir(parents=True,exist_ok=False)
 pack=load_pack(args.root/'审核器/rulepacks/drafts/semantic-v1.4.0')
 model=args.root/'审核器/models/bge-small-zh-v1.5';cache=args.output/'vectors.sqlite3'
 network_denied=False
 try:
  with socket.create_connection(('1.1.1.1',443),timeout=0.5):pass
 except PermissionError:network_denied=True
 except OSError:pass
 encoder=LocalEncoder(model,cache,threads=2,expected_manifest=pack['semantic_config']['model_manifest_sha256'])
 prototypes=pack['semantic_lexicon']['prototypes'];results=[]
 for split,examples in [('development',DEVELOPMENT),('validation',VALIDATION)]:
  # Batch distinct inputs before individual ranking to measure a realistic cached candidate set.
  encoder.encode([*[e['text'] for e in prototypes],*[t for _,t in examples]])
  for expected,text in examples:
   rank=encoder.rank(text,prototypes)
   direct=interpret(text,pack['semantic_lexicon'])
   results.append({'split':split,'text':text,'expected':expected,'top':rank[0],
                   'top1_correct':rank[0]['concept']==expected,'rule_state':direct.state,
                   'model_decision':'candidate_only'})
 pairs=[]
 vectors=encoder.encode([s for a,b,_,_ in PAIRS for s in [a,b]])
 for i,(a,b,equal,kind) in enumerate(PAIRS):
  pairs.append({'left':a,'right':b,'equivalent_in_given_context':equal,'type':kind,
                'score':float(vectors[2*i] @ vectors[2*i+1]),'automatic_pass':False})
 cold=encoder.stats.copy();encoder.close()
 start=time.perf_counter();cached=LocalEncoder(model,cache,threads=2,expected_manifest=pack['semantic_config']['model_manifest_sha256'])
 cached.encode([*[e['text'] for e in prototypes],*[t for _,t in DEVELOPMENT+VALIDATION],*[s for a,b,_,_ in PAIRS for s in [a,b]]])
 warm={**cached.stats,'reload_and_replay_seconds':time.perf_counter()-start}
 overlong=False
 try:cached.encode(['控制措施'*600])
 except SemanticUnavailable:overlong=True
 cached.close()
 summary={'model_id':'BAAI/bge-small-zh-v1.5','machine':platform.platform(),'cpu':'Apple M4 Max','physical_memory_GiB':128,
          'ordinary_laptop_measured':False,'network_denied_by_os':network_denied,'threads':2,
          'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'cold':cold,'cache_replay':warm,
          'model_bundle_bytes':sum(f.stat().st_size for f in model.iterdir() if f.is_file()),
          'overlong_rejected_without_truncation':overlong,
          'development_top1':[sum(r['top1_correct'] for r in results if r['split']=='development'),len(DEVELOPMENT)],
          'validation_top1':[sum(r['top1_correct'] for r in results if r['split']=='validation'),len(VALIDATION)],
          'labels':'开发人员预先编写的表达样本；用于功能验证，不代表独立人工审核准确率',
          'scores_do_not_authorize_equivalence':True}
 for name,data in [('summary.json',summary),('classification.json',results),('hard_negative_pairs.json',pairs)]:
  (args.output/name).write_text(json.dumps(data,ensure_ascii=False,indent=2))
 print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
