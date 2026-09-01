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
});
