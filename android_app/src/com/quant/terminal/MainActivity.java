package com.quant.terminal;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

public class MainActivity extends Activity {
    private WebView mWebView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 沉浸式暗黑全屏状态栏与导航栏
        Window window = getWindow();
        window.clearFlags(WindowManager.LayoutParams.FLAG_TRANSLUCENT_STATUS);
        window.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS);
        window.setStatusBarColor(Color.parseColor("#0B0E14"));
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            window.setNavigationBarColor(Color.parseColor("#12161F"));
        }

        mWebView = new WebView(this);
        setContentView(mWebView);

        mWebView.setBackgroundColor(Color.parseColor("#0B0E14"));
        mWebView.setLayerType(View.LAYER_TYPE_HARDWARE, null);
        mWebView.clearCache(true);

        WebSettings settings = mWebView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setDatabaseEnabled(true);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);

        // 允许跨域文件访问以加载本地 bundle.json
        settings.setAllowFileAccessFromFileURLs(true);
        settings.setAllowUniversalAccessFromFileURLs(true);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }

        mWebView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                // 子 frame (iframe) 请求绝不拦截，避免 iframe 导航顶替整个主 Activity
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N && !request.isForMainFrame()) {
                    return false;
                }
                String url = request.getUrl().toString();
                return handleUrlOverride(view, url);
            }

            @SuppressWarnings("deprecation")
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return handleUrlOverride(view, url);
            }

            private boolean handleUrlOverride(WebView view, String url) {
                if (url == null) return false;
                // 本地 asset 资源直接放行加载，不接管
                if (url.startsWith("file:///android_asset/")) {
                    return false;
                }
                // 外部网页呼出系统浏览器打开
                if (url.startsWith("http://") || url.startsWith("https://")) {
                    try {
                        Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                        startActivity(intent);
                        return true;
                    } catch (Exception e) {
                        return false;
                    }
                }
                return false;
            }
        });

        mWebView.setWebChromeClient(new WebChromeClient());

        // 加载内置离线资产
        mWebView.loadUrl("file:///android_asset/index.html");
    }

    @Override
    public void onBackPressed() {
        if (mWebView != null) {
            // 优先拦截并关闭前端弹窗（如研报原篇、历史日期弹窗）
            mWebView.evaluateJavascript(
                "(function() { " +
                "  var mask = document.getElementById('modal-report-mask'); " +
                "  if (mask && mask.classList.contains('on')) { closeReportModal(); return '1'; } " +
                "  var dateMask = document.getElementById('modal-date-mask'); " +
                "  if (dateMask && dateMask.classList.contains('on')) { closeDateModal(); return '1'; } " +
                "  return '0'; " +
                "})()",
                result -> {
                    if ("\"1\"".equals(result) || "1".equals(result)) {
                        return; // 已拦截弹窗关闭
                    }
                    if (mWebView.canGoBack()) {
                        mWebView.goBack();
                    } else {
                        MainActivity.super.onBackPressed();
                    }
                }
            );
            return;
        }
        super.onBackPressed();
    }
}
