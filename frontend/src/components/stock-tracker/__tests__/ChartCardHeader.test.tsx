import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ChartCardHeader } from "../ChartCardHeader";

describe("ChartCardHeader", () => {
  it("renders the title and the help text (as tooltip content)", () => {
    render(<ChartCardHeader title="RPS trend" helpText="Explains RPS." />);

    expect(screen.getByText("RPS trend")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Explains RPS." })).toBeInTheDocument();
    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toHaveTextContent("Explains RPS.");
    expect(tooltip.className).toContain("invisible");
  });

  it("renders the meta text when provided", () => {
    render(<ChartCardHeader title="Fund flow" helpText="Help." meta="数据日期 2026-08-31" />);

    expect(screen.getByText("数据日期 2026-08-31")).toBeInTheDocument();
    expect(screen.getByText("Fund flow")).toBeInTheDocument();
  });

  it("omits the meta text when not provided", () => {
    const { container } = render(<ChartCardHeader title="Fund flow" helpText="Help." />);

    // No <span> is rendered in the header when meta is absent.
    expect(container.querySelector("span")).toBeNull();
  });

  it("renders custom actions next to the help button", () => {
    render(
      <ChartCardHeader
        title="Fund flow"
        helpText="Help."
        actions={<button type="button">Wide</button>}
      />,
    );

    expect(screen.getByRole("button", { name: "Wide" })).toBeInTheDocument();
    // The help button still renders alongside.
    expect(screen.getByRole("button", { name: "Help." })).toBeInTheDocument();
  });
});
