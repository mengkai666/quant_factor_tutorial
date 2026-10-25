import 'package:flutter/material.dart';
import '../theme/app_theme.dart';

/// 多空状态呼吸指示灯 (🟢 顺势绿灯 / 🟡 震荡黄灯 / 🔴 禁区红灯)
class StatusLampWidget extends StatelessWidget {
  final String status; // 'green', 'yellow', 'red'
  final String? customLabel;

  const StatusLampWidget({
    super.key,
    required this.status,
    this.customLabel,
  });

  @override
  Widget build(BuildContext context) {
    Color bg;
    Color border;
    Color text;
    String label;

    switch (status.toLowerCase()) {
      case 'green':
        bg = AppTheme.fallGreen.withOpacity(0.15);
        border = AppTheme.fallGreen.withOpacity(0.4);
        text = AppTheme.fallGreen;
        label = customLabel ?? '🟢 顺势绿灯';
        break;
      case 'red':
        bg = AppTheme.riseRed.withOpacity(0.15);
        border = AppTheme.riseRed.withOpacity(0.4);
        text = AppTheme.riseRed;
        label = customLabel ?? '🔴 禁区红灯';
        break;
      case 'yellow':
      default:
        bg = AppTheme.warningAmber.withOpacity(0.15);
        border = AppTheme.warningAmber.withOpacity(0.4);
        text = AppTheme.warningAmber;
        label = customLabel ?? '🟡 震荡观察';
        break;
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: border),
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 10,
          fontWeight: FontWeight.w700,
          color: text,
        ),
      ),
    );
  }
}
