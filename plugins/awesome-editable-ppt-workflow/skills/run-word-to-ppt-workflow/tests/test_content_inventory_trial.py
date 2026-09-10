"""Offline check: source clauses cannot disappear before Image2 compilation."""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from complex_page_experiment.director import _validate_content_inventory, _director_relative
from complex_page_experiment.consulting_prompt import _page_plan_architecture

def main():
    blocks = [{'source_block_id':'b1','type':'paragraph','text':'已签约；实缴金额尚未披露。'},
              {'source_block_id':'b2','type':'table','rows':[['能力','问题','目标'],['GP管理','如何选人','动态优胜劣汰']]}]
    inventory = [dict(source_block_id=b['source_block_id'],source_quote=q,display_copy=q,
                      information_role='fact',priority='supporting',reading_order=i+1,placement='同页支持区')
                 for i,(b,q) in enumerate([(blocks[0],blocks[0]['text']),
                     (blocks[1],'能力 | 问题 | 目标'),(blocks[1],'GP管理 | 如何选人 | 动态优胜劣汰')])]
    plan = {'content_inventory':inventory,'relationship_analysis':[], 'priority_rationale':'能力提升是重点',
            'sequence_context':'承接已有体系，说明管理能力', 'page_purpose':'支持管理判断',
            'primary_relationship':{'grammar':'composition_architecture','fact_ids':['b1','b2'],'nodes':[],'edges':[]},
            'core_exhibit':{'grammar':'analytical_table','fact_ids':['b2']},'support_groups':[],
            'reading_path':'先背景再重点','local_visuals':[]}
    view = SimpleNamespace(value={'complete_word_content':blocks})
    _validate_content_inventory(plan,view)
    rendered = _page_plan_architecture({'page_plan':plan,'selected_references':[]},view)
    assert '实缴金额尚未披露' in rendered and '动态优胜劣汰' in rendered
    inventory[0]['source_quote']='已签约'
    try: _validate_content_inventory(plan,view)
    except ValueError as exc: assert 'omits' in str(exc)
    else: raise AssertionError('omitted qualifier accepted')
    inventory[0]['source_quote']='已实缴'
    try: _validate_content_inventory(plan,view)
    except ValueError as exc: assert 'source-exact' in str(exc)
    else: raise AssertionError('invented source accepted')
    ws = SimpleNamespace(experiment_id='offline')
    assert _director_relative(ws,1) != _director_relative(ws,2)
    print('content inventory and compiler: PASS')

if __name__ == '__main__': main()
