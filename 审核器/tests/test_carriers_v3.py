import pytest
from risk_audit.checks.carriers_v3 import parse_carrier_references_v3 as parse, local_carrier_aliases
from risk_audit.checks.duty_details import responsibility_phrase_v3
from risk_audit.models import Record,FieldValue
from risk_audit.checks.registry import CheckContext


def test_basis_quote_is_not_a_carrier_but_explicit_quote_still_is():
    text='按照《会议管理办法》要求开展会议审批，对会议申请单的准确性负有审核责任。'
    r=parse(text,allowed={'会议申请单'})
    assert '会议管理办法' not in r['references'] and '会议申请单' in r['references']
    assert any(x['kind']=='policy_basis' for x in r['evidence'])
    r=parse(text+'对《会议管理办法》的完整性负有审核责任。',allowed={'会议申请单'})
    assert '会议管理办法' in r['unresolved']
    r=parse(text,allowed={'会议申请单','会议管理办法'})
    assert '会议管理办法' in r['references']


@pytest.mark.parametrize('text',[
    '对主办部门提交的会议申请单中会议时间的准确性负有审核责任。',
    '对出差申请选择原单位成本中心的准确性负有主体责任。',
])
def test_actions_do_not_require_all_remaining_words_to_be_document_names(text):
    assert parse(text,allowed={'会议申请单'})['status']!='unavailable'


@pytest.mark.parametrize('text',[
    '对提交未知报告的准确性负有审核责任。',
    '对选择成本中心及未知登记表的准确性负有主体责任。',
    '对会议申请单及其他公司专用报告的完整性负有审核责任。',
])
def test_unknown_documents_remain_unresolved(text):
    assert parse(text,allowed={'会议申请单'})['status'] in {'unavailable','unresolved'}


def test_parenthetical_display_name_is_local_to_current_allowed_set():
    text='对技术鉴定报告的准确性负有审核责任。'
    assert parse(text,allowed={'技术鉴定报告(含拟退役技术鉴定表)'})['status']=='matched'
    assert parse(text,allowed={'其他技术鉴定报告'})['status']=='unresolved'
    assert local_carrier_aliases({'技术鉴定报告(设备)','技术鉴定报告(车辆)'},{})=={}


def test_alternative_documents_are_explicitly_in_the_same_list_item():
    r=parse('对合同的准确性负有审核责任。',allowed={'合同或协议'})
    assert r['references']==['合同或协议']
    assert parse('对采购合同的准确性负有审核责任。',allowed={'合同或协议'})['status'] in {'unavailable','unresolved'}


def test_document_content_is_not_an_independent_carrier_from_vocabulary():
    r=parse('对拆除计划清单及可研批复文件的退役资产拆除计划准确性负有审核责任。',
            allowed={'拆除计划清单','可研批复文件'},terminology={'退役资产拆除计划'})
    assert '退役资产拆除计划' not in r['references']


def test_action_cannot_swallow_unrecognized_invoice_or_order():
    r=parse('对报销特殊发票及采购订单物品对应关系负有主体责任。',allowed={'费用明细表'})
    assert r['status'] in {'unavailable','unresolved'}


def test_concatenated_complete_carrier_names_are_not_one_invented_name():
    r=parse('对项目明细表固定资产卡片的两年内项目挂接准确性负有审核责任。',allowed={'项目明细表','固定资产卡片'})
    assert r['status']=='matched' and r['references']==['固定资产卡片','项目明细表']
    r=parse('对项目明细表未知资产卡片的准确性负有审核责任。',allowed={'项目明细表','固定资产卡片'})
    assert r['status'] in {'unresolved','unavailable'}


def test_sequence_segmentation_does_not_allow_vocabulary_only_documents():
    r=parse('对项目明细表未知资产卡片的准确性负有审核责任。',allowed={'项目明细表'},terminology={'未知资产卡片'})
    assert '未知资产卡片' in r['unresolved']


@pytest.mark.parametrize('text,okay',[
    ('对发票名称准确、金额及税率与合同一致负有主体责任。',True),
    ('对申请单真实完整负责。',True),
    ('负责审核申请单。',False),
    ('对申请单准确不负责任。',False),
])
def test_responsibility_adjectives_keep_responsibility_and_negation_checks(text,okay):
    r=Record('position_duty','E','06','default','a','岗位',3,{'duty':FieldValue(text,text,'A3')},'id')
    ctx=CheckContext([r],[],[r],{}, {}, {},[],[])
    findings=responsibility_phrase_v3(ctx,{'attributes':['准确性','真实性','完整性','一致性']})
    assert (not findings)==okay
