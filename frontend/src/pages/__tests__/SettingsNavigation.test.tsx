import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Settings } from "../Settings";
import { getNavVisibility } from "@/lib/navVisibility";

const apiMock = vi.hoisted(() => ({
  getLLMSettings: vi.fn(),
  getDataSourceSettings: vi.fn(),
  getChannelStatus: vi.fn(),
  startChannels: vi.fn(),
  stopChannels: vi.fn(),
  updateLLMSettings: vi.fn(),
  updateDataSourceSettings: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: apiMock,
    isAuthRequiredError: vi.fn(() => false),
  };
});

vi.mock("@/lib/apiAuth", () => ({
  getApiAuthKey: vi.fn(() => ""),
  setApiAuthKey: vi.fn(),
}));

function llmSettings() {
  return {
    provider: "openrouter",
    model_name: "deepseek/deepseek-v3.2",
    base_url: "https://openrouter.ai/api/v1",
    api_key_env: "OPENROUTER_API_KEY",
    api_key_configured: false,
    api_key_required: true,
    temperature: 0.1,
    timeout_seconds: 120,
    max_retries: 2,
    reasoning_effort: "",
    sse_timeout_seconds: 300,
    env_path: "agent/.env",
    providers: [
      {
        name: "openrouter",
        label: "OpenRouter",
        api_key_env: "OPENROUTER_API_KEY",
        base_url_env: "OPENROUTER_BASE_URL",
        default_model: "deepseek/deepseek-v3.2",
        default_base_url: "https://openrouter.ai/api/v1",
        api_key_required: true,
        auth_type: "api_key",
      },
    ],
  };
}

function dataSourceSettings() {
  return {
    tushare_token_configured: false,
    baostock_supported: true,
    baostock_installed: true,
    baostock_message: "BaoStock available",
    env_path: "agent/.env",
  };
}

function channelStatus() {
  return {
    running: false,
    inbound_queue: 0,
    outbound_queue: 0,
    session_count: 0,
    channels: {},
  };
}

describe("Settings navigation panel", () => {
  beforeEach(() => {
    window.localStorage.clear();
    apiMock.getLLMSettings.mockResolvedValue(llmSettings());
    apiMock.getDataSourceSettings.mockResolvedValue(dataSourceSettings());
    apiMock.getChannelStatus.mockResolvedValue(channelStatus());
  });

  it("renders toggles for every sidebar item", async () => {
    render(<Settings />);

    expect(await screen.findByText("Navigation")).toBeInTheDocument();
    expect(screen.getByText("Options Lab")).toBeInTheDocument();
    expect(screen.getByText("Agent")).toBeInTheDocument();
    expect(screen.getByText("Alpha Zoo")).toBeInTheDocument();
  });

  it("persists toggled visibility to localStorage", async () => {
    render(<Settings />);

    expect(await screen.findByText("Navigation")).toBeInTheDocument();

    const optionsRow = screen.getByText("Options Lab").closest("label");
    const toggle = optionsRow?.querySelector('button[role="switch"]');
    expect(toggle).toHaveAttribute("aria-checked", "true");

    fireEvent.click(toggle!);

    await waitFor(() => {
      expect(getNavVisibility()).toEqual({ "/options": false });
    });
    expect(toggle).toHaveAttribute("aria-checked", "false");
  });
});
