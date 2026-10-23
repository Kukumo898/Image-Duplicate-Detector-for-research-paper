"""The public CSV tool must not count annotations/abstention as detections."""
from scripts.evaluate import metrics,pair_prediction


def test_public_evaluator_three_classes_and_unknown_are_not_bool_findings():
    for category in ('annotation_only','inconclusive'):
        assert pair_prediction({'findings':[{'evidence_class':category}],'diagnostics':{}})==category
    assert pair_prediction({'findings':[{'kind':'exact_pixels'}],'diagnostics':{}})=='inconclusive'
    assert pair_prediction({'findings':[],'diagnostics':{'status':'insufficient_information'}})=='inconclusive'
    assert pair_prediction({'findings':[],'diagnostics':{'status':'no_verified_match'}})=='no_match'
    assert pair_prediction({'findings':[{'evidence_class':'annotation_only'},{'evidence_class':'data_supported'}],'diagnostics':{}})=='data_supported'


def test_public_evaluator_abstentions_keep_denominator_without_inventing_tn():
    result=metrics([{'label':True,'detected':False,'abstained':True},
                    {'label':False,'detected':False,'abstained':True},
                    {'label':False,'detected':False,'abstained':False},
                    {'label':True,'detected':True,'abstained':False}])
    assert (result['tp'],result['fp'],result['fn'],result['tn'])==(1,0,1,1)
    assert result['positive_abstentions']==result['negative_abstentions']==1
    assert result['coverage']==.5
    assert result['recall']==.5
    assert result['false_positive_rate'] is None
    assert result['false_alarm_fraction_full_negative_denominator']==0
