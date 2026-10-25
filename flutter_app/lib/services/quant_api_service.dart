import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../models/bundle_model.dart';

/// 量化终端 API 服务层 (支持自动局域网嗅探与离线缓存)
class QuantApiService {
  static const String _cacheKey = 'cached_quant_bundle_v1';

  // 预置默认候选端点列表
  static final List<String> defaultEndpoints = [
    'http://192.168.1.7:8088/mobile_app/bundle.json', // 本地 WiFi 局域网真机
    'http://10.0.2.2:8088/mobile_app/bundle.json',    // Android 模拟器回环
    'http://localhost:8088/mobile_app/bundle.json',   // Windows / iOS 模拟器
  ];

  /// 获取最新数据 (优先从远端拉取，失败时回退至本地缓存)
  static Future<QuantBundle> fetchBundle({String? customUrl}) async {
    final candidateUrls = customUrl != null
        ? [customUrl, ...defaultEndpoints]
        : defaultEndpoints;

    for (final url in candidateUrls) {
      try {
        final uri = Uri.parse('$url?t=${DateTime.now().millisecondsSinceEpoch}');
        final response = await http.get(uri).timeout(const Duration(seconds: 4));

        if (response.statusCode == 200) {
          final data = json.decode(utf8.decode(response.bodyBytes));
          final bundle = QuantBundle.fromJson(data);
          // 异步写入缓存
          _saveToCache(response.body);
          return bundle;
        }
      } catch (_) {
        // 继续尝试下一个候选端点
      }
    }

    // 所有网络端点均无法连通，尝试加载本地离线缓存
    final cachedJson = await _loadFromCache();
    if (cachedJson != null) {
      final data = json.decode(cachedJson);
      return QuantBundle.fromJson(data);
    }

    throw Exception('无法连接至量化数据服务，且本地无有效离线缓存');
  }

  /// 触发后端刷新最新数据
  static Future<bool> triggerBackendRefresh({String baseUrl = 'http://192.168.1.7:8088'}) async {
    try {
      final uri = Uri.parse('$baseUrl/api/refresh');
      final response = await http.get(uri).timeout(const Duration(seconds: 12));
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  static Future<void> _saveToCache(String jsonStr) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_cacheKey, jsonStr);
    } catch (_) {}
  }

  static Future<String?> _loadFromCache() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      return prefs.getString(_cacheKey);
    } catch (_) {
      return null;
    }
  }
}
