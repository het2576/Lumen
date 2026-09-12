"use client";

import { useState } from "react";

export interface ChartDataPoint {
  label: string;
  value: number;
  formattedValue?: string;
  color?: string;
}

export interface LumenChartProps {
  type?: "bar" | "line" | "donut" | "pie";
  title?: string;
  subtitle?: string;
  data: ChartDataPoint[];
}

const PALETTE = [
  "#81e4ca", // Lumen Signal mint
  "#7fa2f8", // Soft cobalt
  "#f2ca77", // Warm amber
  "#ff8f7c", // Coral
  "#c084fc", // Purple
  "#38bdf8", // Sky blue
  "#fb7185", // Rose
];

export default function LumenChart({
  type = "bar",
  title,
  subtitle,
  data,
}: LumenChartProps) {
  const [chartType, setChartType] = useState<"bar" | "donut" | "line">(
    type === "pie" ? "donut" : type
  );
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  if (!data || data.length === 0) {
    return null;
  }

  // Filter out invalid numbers and calculate metrics
  const validData = data.filter((d) => !isNaN(d.value) && isFinite(d.value));
  if (validData.length === 0) return null;

  const maxValue = Math.max(...validData.map((d) => Math.abs(d.value)), 1);
  const totalValue = validData.reduce((acc, d) => acc + Math.abs(d.value), 0);

  return (
    <div className="lumen-chart-card">
      <div className="lumen-chart-header">
        <div>
          {title && <h4 className="lumen-chart-title">{title}</h4>}
          {subtitle && <p className="lumen-chart-subtitle">{subtitle}</p>}
        </div>
        <div className="lumen-chart-tabs" role="tablist" aria-label="Chart type">
          <button
            type="button"
            className={`lumen-chart-tab ${chartType === "bar" ? "active" : ""}`}
            onClick={() => setChartType("bar")}
            title="Bar Chart"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 20V10M18 20V4M6 20v-4" />
            </svg>
            <span>Bar</span>
          </button>
          <button
            type="button"
            className={`lumen-chart-tab ${chartType === "line" ? "active" : ""}`}
            onClick={() => setChartType("line")}
            title="Line Trend"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 18l6-6 4 4 8-8" />
            </svg>
            <span>Line</span>
          </button>
          <button
            type="button"
            className={`lumen-chart-tab ${chartType === "donut" ? "active" : ""}`}
            onClick={() => setChartType("donut")}
            title="Donut / Distribution"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="9" />
              <circle cx="12" cy="12" r="4" />
            </svg>
            <span>Donut</span>
          </button>
        </div>
      </div>

      <div className="lumen-chart-body">
        {chartType === "bar" && (
          <div className="lumen-chart-bars">
            {validData.map((item, index) => {
              const pct = Math.min(100, Math.max(8, (Math.abs(item.value) / maxValue) * 100));
              const color = item.color || PALETTE[index % PALETTE.length];
              const isHovered = hoveredIndex === index;

              return (
                <div
                  key={index}
                  className={`lumen-bar-row ${isHovered ? "is-hovered" : ""}`}
                  onMouseEnter={() => setHoveredIndex(index)}
                  onMouseLeave={() => setHoveredIndex(null)}
                >
                  <div className="lumen-bar-label" title={item.label}>
                    {item.label}
                  </div>
                  <div className="lumen-bar-track">
                    <div
                      className="lumen-bar-fill"
                      style={{
                        width: `${pct}%`,
                        backgroundColor: color,
                        boxShadow: isHovered ? `0 0 12px ${color}88` : undefined,
                      }}
                    />
                  </div>
                  <div className="lumen-bar-val">
                    {item.formattedValue || item.value.toLocaleString()}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {chartType === "line" && (
          <div className="lumen-chart-svg-wrap">
            <svg viewBox="0 0 500 180" className="lumen-line-svg" preserveAspectRatio="none">
              <defs>
                <linearGradient id="lineGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#81e4ca" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="#81e4ca" stopOpacity="0.0" />
                </linearGradient>
              </defs>

              {/* Grid lines */}
              <line x1="40" y1="20" x2="480" y2="20" stroke="rgba(255,255,255,0.06)" strokeDasharray="3 3" />
              <line x1="40" y1="80" x2="480" y2="80" stroke="rgba(255,255,255,0.06)" strokeDasharray="3 3" />
              <line x1="40" y1="140" x2="480" y2="140" stroke="rgba(255,255,255,0.06)" strokeDasharray="3 3" />

              {/* Line path */}
              {(() => {
                const count = validData.length;
                const step = count > 1 ? (480 - 50) / (count - 1) : 0;
                const points = validData.map((d, i) => {
                  const x = 50 + i * step;
                  const y = 140 - (Math.abs(d.value) / maxValue) * 110;
                  return { x, y, d };
                });

                const pathD = points.reduce(
                  (acc, pt, i) => (i === 0 ? `M ${pt.x} ${pt.y}` : `${acc} L ${pt.x} ${pt.y}`),
                  ""
                );
                const areaD = `${pathD} L ${points[points.length - 1].x} 140 L ${points[0].x} 140 Z`;

                return (
                  <>
                    <path d={areaD} fill="url(#lineGrad)" />
                    <path d={pathD} fill="none" stroke="#81e4ca" strokeWidth="2.5" strokeLinecap="round" />
                    {points.map((pt, i) => (
                      <g key={i}>
                        <circle
                          cx={pt.x}
                          cy={pt.y}
                          r={hoveredIndex === i ? 6 : 4}
                          fill="#0d1118"
                          stroke="#81e4ca"
                          strokeWidth="2"
                          style={{ cursor: "pointer", transition: "r 0.15s ease" }}
                          onMouseEnter={() => setHoveredIndex(i)}
                          onMouseLeave={() => setHoveredIndex(null)}
                        />
                      </g>
                    ))}
                  </>
                );
              })()}
            </svg>

            {/* Labels under the line chart */}
            <div className="lumen-line-labels">
              {validData.map((d, i) => (
                <div key={i} className="lumen-line-label-col">
                  <span className="lumen-line-label-name" title={d.label}>
                    {d.label}
                  </span>
                  <span className="lumen-line-label-val">
                    {d.formattedValue || d.value.toLocaleString()}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {chartType === "donut" && (
          <div className="lumen-donut-layout">
            <div className="lumen-donut-graphic">
              <svg viewBox="0 0 160 160" width="170" height="170">
                {(() => {
                  let accumulated = 0;
                  const radius = 56;
                  const circumference = 2 * Math.PI * radius;

                  return validData.map((item, index) => {
                    const share = Math.abs(item.value) / (totalValue || 1);
                    const strokeDash = `${share * circumference} ${circumference}`;
                    const strokeOffset = -accumulated * circumference;
                    accumulated += share;
                    const color = item.color || PALETTE[index % PALETTE.length];
                    const isHovered = hoveredIndex === index;

                    return (
                      <circle
                        key={index}
                        cx="80"
                        cy="80"
                        r={radius}
                        fill="transparent"
                        stroke={color}
                        strokeWidth={16}
                        strokeDasharray={strokeDash}
                        strokeDashoffset={strokeOffset}
                        transform="rotate(-90 80 80)"
                        style={{
                          transition: "opacity 0.2s ease",
                          cursor: "pointer",
                          opacity: hoveredIndex !== null && !isHovered ? 0.45 : 1,
                        }}
                        onMouseEnter={() => setHoveredIndex(index)}
                        onMouseLeave={() => setHoveredIndex(null)}
                      />
                    );
                  });
                })()}
                <text x="80" y="75" textAnchor="middle" fill="var(--paper)" fontSize="18" fontWeight="700">
                  {validData.length}
                </text>
                <text x="80" y="91" textAnchor="middle" fill="var(--quiet)" fontSize="10" letterSpacing="0.08em">
                  ITEMS
                </text>
              </svg>
            </div>

            <div className="lumen-donut-legend">
              {validData.map((item, index) => {
                const color = item.color || PALETTE[index % PALETTE.length];
                const pct = Math.round((Math.abs(item.value) / (totalValue || 1)) * 100);
                const isHovered = hoveredIndex === index;

                return (
                  <div
                    key={index}
                    className={`lumen-legend-item ${isHovered ? "is-hovered" : ""}`}
                    onMouseEnter={() => setHoveredIndex(index)}
                    onMouseLeave={() => setHoveredIndex(null)}
                  >
                    <span className="lumen-legend-dot" style={{ backgroundColor: color }} />
                    <span className="lumen-legend-name" title={item.label}>
                      {item.label}
                    </span>
                    <span className="lumen-legend-pct">{pct}%</span>
                    <span className="lumen-legend-val">
                      {item.formattedValue || item.value.toLocaleString()}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {hoveredIndex !== null && validData[hoveredIndex] && (
        <div className="lumen-chart-tooltip">
          <span className="tooltip-dot" style={{ backgroundColor: PALETTE[hoveredIndex % PALETTE.length] }} />
          <strong>{validData[hoveredIndex].label}:</strong>
          <span>
            {validData[hoveredIndex].formattedValue ||
              validData[hoveredIndex].value.toLocaleString()}
          </span>
        </div>
      )}
    </div>
  );
}
