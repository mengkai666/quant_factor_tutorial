import 'package:flutter/material.dart';
import '../models/bundle_model.dart';
import '../theme/app_theme.dart';

/// Tab 4: 实战推演与预案
class TabStrategyScreen extends StatelessWidget {
  final StrategyPlan strategy;

  const TabStrategyScreen({super.key, required this.strategy});

  Color _parseColor(String hex, Color fallback) {
    try {
      final clean = hex.replaceAll('#', '');
      if (clean.length == 6) {
        return Color(int.parse('0xFF$clean'));
      }
    } catch (_) {}
    return fallback;
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        // 1. 策略总纲卡片
        Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: AppTheme.surface,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: AppTheme.border),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    strategy.title,
                    style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: Colors.white),
                  ),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: AppTheme.warningAmber.withOpacity(0.15),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: AppTheme.warningAmber.withOpacity(0.3)),
                    ),
                    child: Text(
                      '建议仓位: ${strategy.recommendedPosition}',
                      style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: AppTheme.warningAmber),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Text(
                '🎯 ${strategy.actionSlogan}',
                style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w800, color: Colors.white),
              ),
              if (strategy.rules.isNotEmpty) ...[
                const SizedBox(height: 14),
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: AppTheme.surfaceSub,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: AppTheme.border),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        '核心执行纪律：',
                        style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: AppTheme.textMuted),
                      ),
                      const SizedBox(height: 8),
                      ...strategy.rules.map((r) => Padding(
                            padding: const EdgeInsets.only(bottom: 6),
                            child: Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                const Text('• ', style: TextStyle(color: AppTheme.accentSky, fontSize: 13)),
                                Expanded(
                                  child: Text(
                                    r,
                                    style: const TextStyle(fontSize: 12, color: AppTheme.textPrimary, height: 1.5),
                                  ),
                                ),
                              ],
                            ),
                          )),
                    ],
                  ),
                ),
              ],
              if (strategy.defenseLine.isNotEmpty) ...[
                const SizedBox(height: 12),
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: AppTheme.riseRed.withOpacity(0.08),
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: AppTheme.riseRed.withOpacity(0.3)),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        '🛡️ 止损与防守红线：',
                        style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: AppTheme.riseRed),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        strategy.defenseLine,
                        style: const TextStyle(fontSize: 11, color: AppTheme.textPrimary, height: 1.4),
                      ),
                    ],
                  ),
                ),
              ],
            ],
          ),
        ),

        // 2. 🎯 重点抱团个股 · 明日实战预案（直接给指令）
        if (strategy.baotuanStockTargets.isNotEmpty) ...[
          const SizedBox(height: 16),
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: AppTheme.surface,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: AppTheme.border),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    const Row(
                      children: [
                        Text('🎯 ', style: TextStyle(fontSize: 14)),
                        Text(
                          '重点抱团个股 · 明日实战预案',
                          style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: Colors.white),
                        ),
                      ],
                    ),
                    Text(
                      '直接给指令 · 结果导向',
                      style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: AppTheme.accentSky),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                ...strategy.baotuanStockTargets.map((s) {
                  final isBlack = s.role.contains('禁买') || s.role.contains('⛔');
                  final cardColor = isBlack ? AppTheme.riseRed.withOpacity(0.08) : AppTheme.surfaceSub;
                  final borderColor = isBlack ? AppTheme.riseRed.withOpacity(0.3) : AppTheme.border;
                  final badgeColor = _parseColor(s.badgeColor, isBlack ? AppTheme.riseRed : AppTheme.accentSky);

                  return Container(
                    margin: const EdgeInsets.only(bottom: 10),
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: cardColor,
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: borderColor),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Text(
                              s.role,
                              style: TextStyle(fontSize: 13, fontWeight: FontWeight.w800, color: badgeColor),
                            ),
                            Container(
                              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                              decoration: BoxDecoration(
                                color: AppTheme.warningAmber.withOpacity(0.15),
                                borderRadius: BorderRadius.circular(4),
                              ),
                              child: Text(
                                '仓位: ${s.pos}',
                                style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: AppTheme.warningAmber),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 6),
                        Text(
                          '筛选逻辑：${s.logic}',
                          style: const TextStyle(fontSize: 11, color: AppTheme.textMuted, height: 1.4),
                        ),
                        const SizedBox(height: 8),
                        Container(
                          padding: const EdgeInsets.all(8),
                          decoration: BoxDecoration(
                            color: Colors.black.withOpacity(0.2),
                            borderRadius: BorderRadius.circular(6),
                            border: Border(left: BorderSide(color: badgeColor, width: 3)),
                          ),
                          child: Text(
                            '指令：${s.action}',
                            style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: AppTheme.textPrimary, height: 1.45),
                          ),
                        ),
                        const SizedBox(height: 6),
                        Text(
                          '止损底线：${s.stop}',
                          style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: AppTheme.riseRed, height: 1.4),
                        ),
                      ],
                    ),
                  );
                }),
              ],
            ),
          ),
        ],

        // 3. ⚡ 重点抱团可转债 · 明日 T+0 深度实战作战预案（直接给指令）
        if (strategy.baotuanCbTargets.isNotEmpty) ...[
          const SizedBox(height: 16),
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: AppTheme.surface,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: AppTheme.border),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    const Row(
                      children: [
                        Text('⚡ ', style: TextStyle(fontSize: 14)),
                        Text(
                          '重点抱团转债 · T+0 深度作战预案',
                          style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: Colors.white),
                        ),
                      ],
                    ),
                    const Text(
                      'T+0 极致机动 · 锚定正股',
                      style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: Color(0xFFA78BFA)),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                ...strategy.baotuanCbTargets.map((c) {
                  final isBlack = c.role.contains('破位出清') || c.role.contains('一票否决') || c.pos.contains('0');
                  final cardColor = isBlack ? AppTheme.riseRed.withOpacity(0.08) : AppTheme.surfaceSub;
                  final borderColor = isBlack ? AppTheme.riseRed.withOpacity(0.3) : AppTheme.border;
                  final badgeColor = _parseColor(c.badgeColor, isBlack ? AppTheme.riseRed : const Color(0xFFA78BFA));

                  return Container(
                    margin: const EdgeInsets.only(bottom: 10),
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: cardColor,
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: borderColor),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Row(
                              children: [
                                Text(
                                  '${c.bondName} (${c.bondCode})',
                                  style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w800, color: Colors.white),
                                ),
                                const SizedBox(width: 6),
                                Text(
                                  c.role,
                                  style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: badgeColor),
                                ),
                              ],
                            ),
                            Container(
                              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                              decoration: BoxDecoration(
                                color: AppTheme.warningAmber.withOpacity(0.15),
                                borderRadius: BorderRadius.circular(4),
                              ),
                              child: Text(
                                '仓位: ${c.pos}',
                                style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: AppTheme.warningAmber),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 6),
                        Text(
                          '联动正股：${c.stockName} (${c.stockPct})',
                          style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: AppTheme.accentSky, height: 1.4),
                        ),
                        const SizedBox(height: 8),
                        Container(
                          padding: const EdgeInsets.all(8),
                          decoration: BoxDecoration(
                            color: Colors.black.withOpacity(0.2),
                            borderRadius: BorderRadius.circular(6),
                            border: Border(left: BorderSide(color: badgeColor, width: 3)),
                          ),
                          child: Text(
                            '操盘指令：\n${c.action}',
                            style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: AppTheme.textPrimary, height: 1.45),
                          ),
                        ),
                        const SizedBox(height: 6),
                        Text(
                          '撤退止损：${c.stop}',
                          style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: AppTheme.riseRed, height: 1.4),
                        ),
                      ],
                    ),
                  );
                }),
              ],
            ),
          ),
        ],
      ],
    );
  }
}
