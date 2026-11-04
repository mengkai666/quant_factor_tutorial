from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from trading_playbook_links import integrate_legacy_entries, BEGIN
from trading_playbook_links import add_review_entry, add_main_entry, remove_main_entry


def test_banner_replaces_and_backup_is_original(tmp_path):
    target=tmp_path/'output/可转债抱团复盘与追踪.html'
    target.parent.mkdir()
    original='<html><body class="original">旧研究</body></html>'
    target.write_text(original,encoding='utf-8')
    integrate_legacy_entries(tmp_path,'2026-10-30','2026-11-02')
    first=target.read_text(encoding='utf-8')
    assert '旧转债案例仅作历史' in first
    assert first.count(BEGIN)==1
    integrate_legacy_entries(tmp_path,'2026-11-02','2026-11-03')
    second=target.read_text(encoding='utf-8')
    assert second.count(BEGIN)==1 and '2026-11-03' in second
    backup=tmp_path/'output/trading_playbooks/legacy_entry_backup'/target.name
    assert backup.read_text(encoding='utf-8')==original


def test_session_review_links_only_to_the_report_session_and_is_idempotent(tmp_path):
    output=tmp_path/'output'
    review=output/'trading_playbooks/review_2026-11-02/index.html'
    review.parent.mkdir(parents=True)
    review.write_text('review',encoding='utf8')
    original='<html><body><!-- war-room-hub:end --></body></html>'
    assert add_review_entry(original,output,'2026-10-30')==original
    result=add_review_entry(original,output,'2026-11-02','../')
    assert 'href="../trading_playbooks/review_2026-11-02/index.html"' in result
    assert '盘中触发与策略收益尚未核验' in result
    assert add_review_entry(result,output,'2026-11-02','../')==result
    integrated=add_main_entry(original,output,'2026-11-02','../')
    assert integrated.count('PLAYBOOK-SESSION-REVIEW-BEGIN')==1
    assert add_main_entry(integrated,output,'2026-11-02','../')==integrated
    assert remove_main_entry(integrated)==original
    assert add_main_entry(original,output,'20261102','../')==integrated
