import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'screens/home_scaffold.dart';
import 'theme/app_theme.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  // 强制沉浸式全屏状态栏与深色系统栏样式
  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.light,
      systemNavigationBarColor: Color(0xFF12161F),
      systemNavigationBarIconBrightness: Brightness.light,
    ),
  );

  runApp(const QuantFactorApp());
}

class QuantFactorApp extends StatelessWidget {
  const QuantFactorApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '量化投研终端',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.darkTheme,
      home: const HomeScaffold(),
    );
  }
}
