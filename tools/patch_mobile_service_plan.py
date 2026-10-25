from pathlib import Path

file_path = Path("src/mobile_service.py")
content = file_path.read_text(encoding="utf-8")

# 1. Update call site
old_call = 'strategy_module = self._build_strategy_plan(degree, degree_status, tianti_data.get("stocks", []))'
new_call = 'strategy_module = self._build_strategy_plan(degree, degree_status, tianti_data.get("stocks", []), today_str, mood_module.get("dt_count", 0))'
assert old_call in content, "old_call not found"
content = content.replace(old_call, new_call, 1)

# 2. Update method implementation
old_method_start = 'def _build_strategy_plan(self, degree: int, status_text: str, top_stocks: List[Dict[str, Any]]) -> Dict[str, Any]:'
new_method = '''def _build_strategy_plan(self, degree: int, status_text: str, top_stocks: List[Dict[str, Any]], date_str: str = "", dt_count: int = 0) -> Dict[str, Any]:
        """根据真实复盘研报或情绪温度动态提取深度实战预案作战看板"""
        try:
            from plan_parser import get_plan_for_date
            return get_plan_for_date(PROJECT_ROOT, date_str or "2026-10-22", degree, status_text, top_stocks, dt_count)
        except Exception as e:
            logger.warning(f"Error extracting rich plan: {e}")
            from plan_parser import synthesize_plan
            return synthesize_plan(date_str or "2026-10-22", degree, status_text, top_stocks, dt_count)'''

idx = content.find(old_method_start)
assert idx != -1, "old_method_start not found"
end_idx = content.find('if __name__ == "__main__":', idx)
assert end_idx != -1, "if __name__ not found"

content = content[:idx] + new_method + "\n\n" + content[end_idx:]

file_path.write_text(content, encoding="utf-8")
print("✅ Updated src/mobile_service.py successfully!")
