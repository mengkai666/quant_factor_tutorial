import 'package:flutter/material.dart';
import '../theme/app_theme.dart';

/// 市场情绪温度计组件 (0-100)
class MoodGaugeWidget extends StatelessWidget {
  final int degree;
  final String statusText;
  final String colorTag;
  final String turnover;
  final String turnoverChange;

  const MoodGaugeWidget({
    super.key,
    required this.degree,
    required this.statusText,
    required this.colorTag,
    required this.turnover,
    required this.turnoverChange,
  });

  Color _getTagColor() {
    switch (colorTag) {
      case 'rose':
        return AppTheme.riseRed;
      case 'emerald':
        return AppTheme.fallGreen;
      case 'amber':
        return AppTheme.warningAmber;
      default:
        return AppTheme.accentSky;
    }
  }

  @override
  Widget build(BuildContext context) {
    final activeColor = _getTagColor();

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
                '市场情绪温度计',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: AppTheme.textMuted,
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: activeColor.withOpacity(0.15),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(color: activeColor.withOpacity(0.3)),
                ),
                child: Text(
                  statusText,
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    color: activeColor,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.baseline,
                textBaseline: TextBaseline.alphabetic,
                children: [
                  Text(
                    '$degree',
                    style: const TextStyle(
                      fontSize: 42,
                      fontWeight: FontWeight.w800,
                      fontFamily: 'monospace',
                      color: Colors.white,
                    ),
                  ),
                  const SizedBox(width: 8),
                  const Text(
                    '度',
                    style: TextStyle(
                      fontSize: 13,
                      color: AppTheme.textMuted,
                    ),
                  ),
                ],
              ),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(
                    turnover,
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w700,
                      color: Colors.white,
                      fontFamily: 'monospace',
                    ),
                  ),
                  Text(
                    turnoverChange.isNotEmpty ? turnoverChange : '两市总成交',
                    style: const TextStyle(
                      fontSize: 11,
                      color: AppTheme.textMuted,
                    ),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 12),
          // 情绪水平条
          ClipRRect(
            borderRadius: BorderRadius.circular(99),
            child: Container(
              height: 6,
              color: AppTheme.border,
              child: FractionallySizedBox(
                alignment: Alignment.centerLeft,
                widthFactor: (degree / 100.0).clamp(0.05, 1.0),
                child: Container(color: activeColor),
              ),
            ),
          ),
          const SizedBox(height: 6),
          const Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('0 冰点', style: TextStyle(fontSize: 10, color: AppTheme.textMuted)),
              Text('50 中性', style: TextStyle(fontSize: 10, color: AppTheme.textMuted)),
              Text('100 亢奋', style: TextStyle(fontSize: 10, color: AppTheme.textMuted)),
            ],
          ),
        ],
      ),
    );
  }
}
