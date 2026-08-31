export type DesktopLocale = "en" | "zh-CN";

export type DesktopMessages = {
  startupFailureTitle: string;
  backendAlreadyRunning: string;
  backendUnexpectedExit: string;
  backendStarting: string;
  backendReady: string;
  backendNotStarted: string;
  backendExitedEarly: string;
  backendHealthTimeout: string;
  backendNotFound: string;
  portUnavailable: string;
  credentialEncryptionUnavailable: string;
  credentialFileUnsupported: string;
  credentialKeyUnsupported: string;
  credentialRequestInvalid: string;
  credentialStoreUnavailable: string;
  closingService: string;
  menuApplication: string;
  menuRestartService: string;
  menuOpenLogs: string;
  menuQuit: string;
  menuView: string;
  menuReload: string;
  menuDeveloperTools: string;
  menuActualSize: string;
  menuZoomIn: string;
  menuZoomOut: string;
  menuFullscreen: string;
  loadingTitle: string;
  loadingMessage: string;
  loadingRetry: string;
  loadingOpenLogs: string;
  loadingFailedTitle: string;
  loadingRestartingTitle: string;
};

export type DesktopRendererLocale = {
  locale: DesktopLocale;
  direction: "ltr" | "rtl";
  messages: Pick<
    DesktopMessages,
    | "loadingTitle"
    | "loadingMessage"
    | "loadingRetry"
    | "loadingOpenLogs"
    | "loadingFailedTitle"
    | "loadingRestartingTitle"
  >;
};

export const supportedDesktopLocales: readonly DesktopLocale[] = [
  "en",
  "zh-CN",
];

const messages: Record<DesktopLocale, DesktopMessages> = {
  en: {
    startupFailureTitle: "Vibe-Trading Desktop failed to start",
    backendAlreadyRunning: "The backend process is already running.",
    backendUnexpectedExit: "The Vibe-Trading backend exited unexpectedly (code {code}).",
    backendStarting: "Backend started; waiting for health check · {port}",
    backendReady: "Local service is ready · {port}",
    backendNotStarted: "The backend has not started.",
    backendExitedEarly: "The Vibe-Trading backend exited before it was ready (code {code}).\n{details}",
    backendHealthTimeout: "Timed out waiting for the Vibe-Trading backend.\n{details}",
    backendNotFound: "Could not find vibe-trading.exe. Install the backend first or set VIBE_TRADING_EXECUTABLE.",
    portUnavailable: "Could not allocate a local port.",
    credentialEncryptionUnavailable: "Credential encryption is unavailable for this Windows user session.",
    credentialFileUnsupported: "The desktop credential file format is not supported.",
    credentialKeyUnsupported: "This credential key is not supported.",
    credentialRequestInvalid: "The credential request is invalid.",
    credentialStoreUnavailable: "Secure credential storage has not been initialized.",
    closingService: "Closing the local service…",
    menuApplication: "Application",
    menuRestartService: "Restart local service",
    menuOpenLogs: "Open log folder",
    menuQuit: "Quit",
    menuView: "View",
    menuReload: "Reload",
    menuDeveloperTools: "Developer tools",
    menuActualSize: "Actual size",
    menuZoomIn: "Zoom in",
    menuZoomOut: "Zoom out",
    menuFullscreen: "Fullscreen",
    loadingTitle: "Starting Vibe-Trading Desktop",
    loadingMessage: "The desktop app is preparing the local service. The first start may take a moment.",
    loadingRetry: "Retry",
    loadingOpenLogs: "Open log folder",
    loadingFailedTitle: "Startup failed",
    loadingRestartingTitle: "Restarting",
  },
  "zh-CN": {
    startupFailureTitle: "Vibe-Trading Desktop 启动失败",
    backendAlreadyRunning: "后端进程已经在运行。",
    backendUnexpectedExit: "Vibe-Trading 后端意外退出（代码 {code}）。",
    backendStarting: "后端已启动，正在等待健康检查 · {port}",
    backendReady: "本地服务已就绪 · {port}",
    backendNotStarted: "后端尚未启动。",
    backendExitedEarly: "Vibe-Trading 后端在就绪前退出（代码 {code}）。\n{details}",
    backendHealthTimeout: "等待 Vibe-Trading 后端就绪超时。\n{details}",
    backendNotFound: "找不到 vibe-trading.exe。请先安装后端，或设置 VIBE_TRADING_EXECUTABLE。",
    portUnavailable: "无法分配本地端口。",
    credentialEncryptionUnavailable: "当前 Windows 用户会话无法使用凭据加密。",
    credentialFileUnsupported: "桌面凭据文件格式不受支持。",
    credentialKeyUnsupported: "不支持该凭据键。",
    credentialRequestInvalid: "凭据请求无效。",
    credentialStoreUnavailable: "安全凭据存储尚未初始化。",
    closingService: "正在关闭本地服务…",
    menuApplication: "应用",
    menuRestartService: "重启本地服务",
    menuOpenLogs: "打开日志文件夹",
    menuQuit: "退出",
    menuView: "视图",
    menuReload: "刷新",
    menuDeveloperTools: "开发者工具",
    menuActualSize: "实际大小",
    menuZoomIn: "放大",
    menuZoomOut: "缩小",
    menuFullscreen: "全屏",
    loadingTitle: "正在启动 Vibe-Trading Desktop",
    loadingMessage: "桌面端正在准备本地服务，第一次启动可能需要一点时间。",
    loadingRetry: "重试",
    loadingOpenLogs: "打开日志文件夹",
    loadingFailedTitle: "启动失败",
    loadingRestartingTitle: "正在重新启动",
  },
};

export function resolveDesktopLocale(rawLocale: string | undefined): DesktopLocale {
  const normalized = (rawLocale ?? "").trim().toLowerCase().replaceAll("_", "-");
  if (normalized === "zh" || normalized.startsWith("zh-cn") || normalized.startsWith("zh-hans")) {
    return "zh-CN";
  }
  return "en";
}

export function getDesktopMessages(locale: DesktopLocale): DesktopMessages {
  return messages[locale];
}

export function getRendererLocale(locale: DesktopLocale): DesktopRendererLocale {
  const localeMessages = getDesktopMessages(locale);
  return {
    locale,
    direction: "ltr",
    messages: {
      loadingTitle: localeMessages.loadingTitle,
      loadingMessage: localeMessages.loadingMessage,
      loadingRetry: localeMessages.loadingRetry,
      loadingOpenLogs: localeMessages.loadingOpenLogs,
      loadingFailedTitle: localeMessages.loadingFailedTitle,
      loadingRestartingTitle: localeMessages.loadingRestartingTitle,
    },
  };
}

export function formatDesktopMessage(
  template: string,
  values: Record<string, string | number | null | undefined>,
): string {
  return template.replace(/\{([a-zA-Z0-9_]+)\}/gu, (_match, key: string) => {
    const value = values[key];
    return value === null || value === undefined ? "" : String(value);
  });
}
