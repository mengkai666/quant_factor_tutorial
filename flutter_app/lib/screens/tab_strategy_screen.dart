import 'package:flutter/material.dart';
import '../models/bundle_model.dart';
import '../theme/app_theme.dart';

/// Tab 4: 实战推演与预案
class TabStrategyScreen extends StatelessWidget {
  final StrategyPlan strategy;

  const TabStrategyScreen({super.key, required this.strategy});

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
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
              const SizedBox(height: 14),

              // 核心纪律列表
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
              const SizedBox(height: 12),

              // 防守与止损红线
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
          ),
        ),
      ],
    );
  }
}
