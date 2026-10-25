from pathlib import Path

p = Path("src/plan_parser.py")
c = p.read_text(encoding="utf-8")
c = c.replace('s0_height = top_stocks[0]["height"]', 's0_height = str(top_stocks[0]["height"])')
c = c.replace('s1_height = top_stocks[1]["height"]', 's1_height = str(top_stocks[1]["height"])')
c = c.replace('s2_height = top_stocks[2]["height"]', 's2_height = str(top_stocks[2]["height"])')
p.write_text(c, encoding="utf-8")

m = Path("src/mobile_service.py")
mc = m.read_text(encoding="utf-8")
mc = mc.replace("return get_plan_for_date(PROJECT_ROOT,", "return get_plan_for_date(Path(__file__).resolve().parent.parent,")
m.write_text(mc, encoding="utf-8")
print("Fixed successfully!")
