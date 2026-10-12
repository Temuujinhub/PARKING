import json

import pytest

from app.services.camera_evidence import camera_evidence, exit_evidence_rejection
from app.services.cgi_poller import _extract_json_blocks


@pytest.mark.parametrize('value',[None, 'bad', 101, -1, float('nan'), True])
def test_unknown_confidence_is_not_vehicle_confidence_or_100(value):
    raw={'TrafficCar':{'PlateNumber':'0666ДОР'},'Object':{'ObjectType':'Plate','Confidence':value},'Vehicle':{'Confidence':92}}
    evidence=camera_evidence(raw)
    assert evidence['plate_confidence'] is None and evidence['vehicle_confidence']==92
    assert exit_evidence_rejection(raw,30)[0]=='plate_confidence_unknown'


def test_actual_incident_confidence_stays_low_and_distinct():
    raw={'TrafficCar':{'PlateNumber':'0636ЧД'},'Object':{'ObjectType':'Plate','Confidence':14},'Vehicle':{'Confidence':92}}
    assert exit_evidence_rejection(raw,30)==('confidence<30',14)
    raw['Object']['Confidence']=0
    assert camera_evidence(raw)['plate_confidence']==0


def test_conflicting_plate_nodes_require_review_and_do_not_expose_arbitrary_payload():
    raw={'Plate':{'PlateNumber':'1234УБА','Confidence':95},'TrafficCar':{'PlateNumber':'9999УБА'},'Password':'never-expose','Url':'secret'}
    evidence=camera_evidence(raw)
    assert evidence['text_conflict'] and exit_evidence_rejection(raw,30)[0]=='plate_metadata_conflict'
    assert 'never-expose' not in str(evidence) and 'secret' not in str(evidence)


@pytest.mark.parametrize('text',['{','}','escaped " { } \\','0666ДОР'])
def test_stream_json_string_braces_and_every_chunk_boundary(text):
    objects=[{'TrafficCar':{'PlateNumber':'1234УБА'},'name':text},{'n':2}]
    wire=b''.join(b'Code=TrafficJunction;data='+json.dumps(o,ensure_ascii=False).encode()+b'\r\n' for o in objects)
    for cut in range(len(wire)+1):
        left,rest=_extract_json_blocks(wire[:cut]);right,rest=_extract_json_blocks(rest+wire[cut:])
        assert left+right==objects


def test_incomplete_oversized_json_is_bounded():
    blocks,rest=_extract_json_blocks(b'data={"incomplete":"'+b'a'*(2*1024*1024))
    assert blocks==[] and rest==b''


def test_entry_and_inner_keep_legacy_fallback_while_exit_stays_strict():
    from app.services.cgi_poller import _plate_from
    from app.routers.lpr_router import _extract_plate
    raw={'TrafficCar':{'PlateNumber':'1234УБА','Confidence':0},
         'Object':{'ObjectType':'Plate','Confidence':14}}
    assert _plate_from(raw)==_extract_plate(raw)==('1234УБА',14)
    assert exit_evidence_rejection(raw,30)==('confidence<30',0)
