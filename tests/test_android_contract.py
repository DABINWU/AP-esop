from shared.reports import DeviceReport,report_data
from relay.db import dumps
from pydantic import ValidationError
import pytest


def event():
    return dict(event_id='android-1',device_id='screen-a',report_seq=1,observed_at='2026-11-06T03:00:00Z',
        assignment_revision=0,actual_version_id=None,current_item_id=None,playback_state='idle',pending_version_id=None,error=None)


def test_legacy_event_normalization_is_identical_after_android_extension():
    original=event()
    assert report_data(DeviceReport(**original))=={**original,'observed_at':'2026-11-06T03:00:00Z'}
    assert 'download_progress' not in report_data(DeviceReport(**original,download_progress=None))


def test_download_progress_is_preserved_without_affecting_actual_playback():
    data=event();data['download_progress']=dict(playlist_version_id='v2',received_bytes=50,total_bytes=100,completed_files=0,total_files=2)
    assert report_data(DeviceReport(**data))['download_progress']==data['download_progress']
    assert report_data(DeviceReport(**data))['actual_version_id'] is None


@pytest.mark.parametrize('field,value',[('received_bytes',101),('completed_files',3),('total_files',0)])
def test_invalid_progress_rejected(field,value):
    progress=dict(playlist_version_id='v2',received_bytes=50,total_bytes=100,completed_files=0,total_files=2);progress[field]=value
    with pytest.raises(ValidationError):DeviceReport(**event(),download_progress=progress)
