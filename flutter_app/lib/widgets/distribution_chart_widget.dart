import 'dart:math';
import 'package:flutter/material.dart';
import '../theme/app_theme.dart';

/// 涨跌家数与涨跌区间直方图组件
class DistributionChartWidget extends StatelessWidget {
  final int upCount;
  final int downCount;
  final int ztCount;
  final int dtCount;
  final double brokenRatio;
  final Map<String, int> bins;

  const DistributionChartWidget({
    super.key,
    required this.upCount,
    required this.downCount,
    required this.ztCount,
    required this.dtCount,
    required this.brokenRatio,
    required this.bins,
  });

  @override
  Widget build(BuildContext context) {
    final total = (upCount + downCount) == 0 ? 1 : (upCount + downCount);
    final upPct = ((upCount / total) * 100).round();

    return Container(
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
              const Text(
                '市场赚钱效应分布',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: AppTheme.textMuted,
                ),
              ),
              Text(
                '涨 $upPct%',
                style: const TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                  color: AppTheme.riseRed,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          // 涨跌家数双色对比条
          ClipRRect(
            borderRadius: BorderRadius.circular(99),
            child: SizedBox(
              height: 8,
              child: Row(
                children: [
                  Expanded(
                    flex: upCount == 0 ? 1 : upCount,
                    child: Container(color: AppTheme.riseRed),
                  ),
                  Expanded(
                    flex: downCount == 0 ? 1 : downCount,
                    child: Container(color: AppTheme.fallGreen),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 8),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                '涨 $upCount',
                style: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  color: AppTheme.riseRed,
                  fontFamily: 'monospace',
                ),
              ),
              Text(
                '涨停 $ztCount · 跌停 $dtCount · 炸板率 $brokenRatio%',
                style: const TextStyle(
                  fontSize: 11,
                  color: AppTheme.textMuted,
                ),
              ),
              Text(
                '跌 $downCount',
                style: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  color: AppTheme.fallGreen,
                  fontFamily: 'monospace',
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),
          // 涨跌区间直方柱状图
          _buildHistogram(),
          const SizedBox(height: 4),
          const Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('-10%', style: TextStyle(fontSize: 9, color: AppTheme.textMuted)),
              Text('-5%', style: TextStyle(fontSize: 9, color: AppTheme.textMuted)),
              Text('0%', style: TextStyle(fontSize: 9, color: AppTheme.textMuted)),
              Text('+5%', style: TextStyle(fontSize: 9, color: AppTheme.textMuted)),
              Text('+10%', style: TextStyle(fontSize: 9, color: AppTheme.textMuted)),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildHistogram() {
    const keys = [
      'down_10', 'down_8', 'down_6', 'down_4', 'down_2',
      'up_2', 'up_4', 'up_6', 'up_8', 'up_10'
    ];
    final maxVal = bins.values.isEmpty ? 50 : max(50, bins.values.reduce(max));

    return SizedBox(
      height: 44,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: keys.map((k) {
          final count = bins[k] ?? 0;
          final isUp = k.startsWith('up');
          final color = isUp ? AppTheme.riseRed : AppTheme.fallGreen;
          final heightFactor = (count / maxVal).clamp(0.08, 1.0);

          return Expanded(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 1.5),
              child: Tooltip(
                message: '$k: $count只',
                child: Container(
                  height: 44 * heightFactor,
                  decoration: BoxDecoration(
                    color: color,
                    borderRadius: const BorderRadius.vertical(top: Radius.circular(2)),
                  ),
                ),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }
}
