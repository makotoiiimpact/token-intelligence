// charts.js — themed ECharts wrappers (Token Intelligence design system)

export const PALETTE = [
  '#19F58C', // green — primary
  '#0066FF', // blue
  '#8F00FF', // purple
  '#FFD600', // yellow
  '#FF423D', // red
  '#00FFE0', // cyan
];

const AXIS_LABEL_COLOR = 'rgba(255, 255, 255, 0.4)';
const AXIS_LINE_COLOR  = 'rgba(255, 255, 255, 0.1)';
const SPLIT_LINE_COLOR = 'rgba(255, 255, 255, 0.05)';
const TEXT_COLOR       = 'rgba(255, 255, 255, 0.6)';

const BASE = {
  backgroundColor: 'transparent',
  textStyle: { color: TEXT_COLOR, fontFamily: 'Red Hat Text' },
  color: PALETTE,
  grid: { left: 36, right: 18, top: 28, bottom: 24, containLabel: true },
};

const X_AXIS = {
  axisLine:  { lineStyle: { color: AXIS_LINE_COLOR } },
  axisLabel: { color: AXIS_LABEL_COLOR, fontFamily: 'Red Hat Text' },
  axisTick:  { show: false },
};

const Y_AXIS = {
  axisLine:  { show: false },
  axisTick:  { show: false },
  splitLine: { lineStyle: { color: SPLIT_LINE_COLOR } },
  axisLabel: { color: AXIS_LABEL_COLOR, fontFamily: 'Red Hat Text' },
};

const TOOLTIP = {
  trigger: 'axis',
  backgroundColor: 'rgba(15, 15, 15, 0.92)',
  borderColor: 'rgba(255, 255, 255, 0.12)',
  borderWidth: 1,
  textStyle: { color: '#FFFFFF', fontFamily: 'Red Hat Text', fontSize: 12 },
  padding: [10, 14],
  extraCssText: 'backdrop-filter: blur(12px); border-radius: 12px;',
};

const LEGEND = {
  textStyle: { color: TEXT_COLOR, fontFamily: 'Red Hat Text' },
  top: 0, right: 0,
  icon: 'roundRect',
  itemWidth: 8, itemHeight: 8,
};

function mount(el) {
  const c = echarts.init(el, null, { renderer: 'svg' });
  const onResize = () => c.resize();
  window.addEventListener('resize', onResize);
  return c;
}

export function lineChart(el, { x, series }) {
  const c = mount(el);
  c.setOption({
    ...BASE,
    tooltip: TOOLTIP,
    legend: LEGEND,
    xAxis: { ...X_AXIS, type: 'category', data: x, boundaryGap: false },
    yAxis: { ...Y_AXIS, type: 'value' },
    series: series.map(s => ({
      ...s, type: 'line', smooth: true, showSymbol: false,
      areaStyle: { opacity: 0.12 }, lineStyle: { width: 2 },
    })),
  });
  return c;
}

/**
 * Area chart with horizontal threshold markers.
 * thresholds: [{ value, color, label }]
 */
export function areaChartWithThresholds(el, { x, values, color, thresholds = [] }) {
  const c = mount(el);
  const fill = color || PALETTE[0];
  c.setOption({
    ...BASE,
    tooltip: {
      ...TOOLTIP,
      valueFormatter: v => Number(v).toLocaleString() + ' tokens',
    },
    xAxis: {
      ...X_AXIS,
      type: 'category',
      data: x,
      boundaryGap: false,
      axisLabel: {
        ...X_AXIS.axisLabel,
        interval: x.length > 20 ? 'auto' : 0,
        rotate: x.length > 12 ? 35 : 0,
      },
    },
    yAxis: {
      ...Y_AXIS,
      type: 'value',
      axisLabel: {
        ...Y_AXIS.axisLabel,
        formatter: v => {
          const n = Math.abs(v);
          if (n >= 1e9) return (v / 1e9).toFixed(1) + 'B';
          if (n >= 1e6) return (v / 1e6).toFixed(1) + 'M';
          if (n >= 1e3) return (v / 1e3).toFixed(0) + 'K';
          return v;
        },
      },
    },
    series: [{
      type: 'line',
      data: values,
      smooth: true,
      showSymbol: false,
      lineStyle: { width: 2, color: fill },
      itemStyle: { color: fill },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: fill + '66' },  // 40%
            { offset: 1, color: fill + '00' },  // 0%
          ],
        },
      },
      markLine: thresholds.length ? {
        symbol: ['none', 'none'],
        silent: true,
        lineStyle: { type: 'dashed', width: 1.5 },
        label: {
          position: 'insideEndTop',
          color: 'rgba(255,255,255,0.6)',
          fontFamily: 'Red Hat Mono',
          fontSize: 10,
        },
        data: thresholds.map(t => ({
          yAxis: t.value,
          lineStyle: { color: t.color },
          label: { formatter: t.label || '', color: t.color },
        })),
      } : undefined,
    }],
  });
  return c;
}

export function barChart(el, { categories, values, color }) {
  const c = mount(el);
  c.setOption({
    ...BASE,
    tooltip: { ...TOOLTIP, axisPointer: { type: 'shadow' } },
    xAxis: {
      ...X_AXIS, type: 'category', data: categories,
      axisLabel: {
        ...X_AXIS.axisLabel, interval: 0,
        rotate: categories.length > 5 ? 25 : 0,
      },
    },
    yAxis: { ...Y_AXIS, type: 'value' },
    series: [{
      type: 'bar', data: values,
      itemStyle: { color: color || PALETTE[0], borderRadius: [6, 6, 0, 0] },
      barMaxWidth: 32,
    }],
  });
  return c;
}

/** Horizontal bar chart (for per-project token comparison). */
export function horizontalBarChart(el, { categories, values, color, formatter }) {
  const c = mount(el);
  c.setOption({
    ...BASE,
    grid: { left: 12, right: 24, top: 12, bottom: 12, containLabel: true },
    tooltip: {
      ...TOOLTIP,
      axisPointer: { type: 'shadow' },
      valueFormatter: formatter || (v => Number(v).toLocaleString()),
    },
    xAxis: {
      ...Y_AXIS, type: 'value',
      axisLabel: {
        ...Y_AXIS.axisLabel,
        formatter: v => {
          const n = Math.abs(v);
          if (n >= 1e9) return (v / 1e9).toFixed(1) + 'B';
          if (n >= 1e6) return (v / 1e6).toFixed(1) + 'M';
          if (n >= 1e3) return (v / 1e3).toFixed(0) + 'K';
          return v;
        },
      },
    },
    yAxis: {
      ...X_AXIS, type: 'category', data: categories,
      inverse: true,
      axisLabel: { ...X_AXIS.axisLabel, fontFamily: 'Red Hat Text' },
    },
    series: [{
      type: 'bar', data: values,
      itemStyle: { color: color || PALETTE[0], borderRadius: [0, 6, 6, 0] },
      barMaxWidth: 18,
    }],
  });
  return c;
}

export function stackedBarChart(el, { categories, series, formatter }) {
  const c = mount(el);
  c.setOption({
    ...BASE,
    tooltip: {
      ...TOOLTIP,
      axisPointer: { type: 'shadow' },
      valueFormatter: formatter || (v => Number(v).toLocaleString()),
    },
    legend: LEGEND,
    xAxis: {
      ...X_AXIS, type: 'category', data: categories,
      axisLabel: {
        ...X_AXIS.axisLabel,
        interval: categories.length > 20 ? 'auto' : 0,
        rotate: categories.length > 12 ? 45 : 0,
      },
    },
    yAxis: { ...Y_AXIS, type: 'value' },
    series: series.map((s, i) => ({
      name: s.name,
      type: 'bar',
      stack: 'total',
      data: s.values,
      itemStyle: { color: s.color || PALETTE[i % PALETTE.length] },
      barMaxWidth: 24,
      emphasis: { focus: 'series' },
    })),
  });
  return c;
}

export function groupedBarChart(el, { categories, series, formatter }) {
  const c = mount(el);
  c.setOption({
    ...BASE,
    tooltip: {
      ...TOOLTIP,
      axisPointer: { type: 'shadow' },
      valueFormatter: formatter || (v => Number(v).toLocaleString()),
    },
    legend: LEGEND,
    xAxis: {
      ...X_AXIS, type: 'category', data: categories,
      axisLabel: {
        ...X_AXIS.axisLabel, interval: 0,
        rotate: categories.length > 5 ? 25 : 0,
      },
    },
    yAxis: { ...Y_AXIS, type: 'value' },
    series: series.map((s, i) => ({
      name: s.name,
      type: 'bar',
      data: s.values,
      itemStyle: { color: s.color || PALETTE[i % PALETTE.length], borderRadius: [6, 6, 0, 0] },
      barMaxWidth: 24,
      emphasis: { focus: 'series' },
    })),
  });
  return c;
}

/**
 * Score-trend chart: category x-axis, value y-axis (0..100),
 * segment colors green/yellow/red by value, threshold bands.
 */
export function scoreTrendChart(el, { x, values }) {
  const c = mount(el);
  const colorFor = v => v >= 80 ? '#19F58C' : v >= 50 ? '#FFD600' : '#FF423D';
  const pieces = [
    { gt: -1, lte: 49,  color: '#FF423D' },
    { gt: 49, lte: 79,  color: '#FFD600' },
    { gt: 79, lte: 100, color: '#19F58C' },
  ];
  c.setOption({
    ...BASE,
    tooltip: {
      ...TOOLTIP,
      valueFormatter: v => Math.round(v) + ' / 100',
    },
    xAxis: {
      ...X_AXIS, type: 'category', data: x, boundaryGap: false,
      axisLabel: {
        ...X_AXIS.axisLabel,
        interval: x.length > 20 ? 'auto' : 0,
        rotate: x.length > 14 ? 30 : 0,
      },
    },
    yAxis: {
      ...Y_AXIS, type: 'value', min: 0, max: 100,
      axisLabel: { ...Y_AXIS.axisLabel, formatter: v => v },
    },
    visualMap: {
      show: false, pieces, dimension: 1, outOfRange: { color: '#FF423D' },
    },
    series: [{
      type: 'line',
      data: values,
      smooth: true,
      showSymbol: true,
      symbolSize: 5,
      itemStyle: { color: v => colorFor(v.value?.[1] ?? v.value ?? 0) },
      lineStyle: { width: 2 },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(25, 245, 140, 0.22)' },
            { offset: 1, color: 'rgba(25, 245, 140, 0)' },
          ],
        },
      },
      markLine: {
        symbol: ['none', 'none'],
        silent: true,
        lineStyle: { type: 'dashed', width: 1 },
        label: {
          position: 'insideEndTop',
          fontFamily: 'Red Hat Mono', fontSize: 10,
        },
        data: [
          { yAxis: 80, lineStyle: { color: '#19F58C' }, label: { formatter: '80 good', color: '#19F58C' } },
          { yAxis: 50, lineStyle: { color: '#FFD600' }, label: { formatter: '50 warn', color: '#FFD600' } },
        ],
      },
    }],
  });
  return c;
}

/**
 * Compound-token accumulation chart for a single session.
 * values: cumulative tokens per turn. markers: {turn, label, color} for
 * correction cycles / thresholds.
 */
export function cumulativeAreaChart(el, { x, values, markers = [], thresholds = [] }) {
  const c = mount(el);
  c.setOption({
    ...BASE,
    tooltip: {
      ...TOOLTIP,
      valueFormatter: v => Number(v).toLocaleString() + ' tokens',
    },
    xAxis: {
      ...X_AXIS, type: 'category', data: x, boundaryGap: false,
      axisLabel: {
        ...X_AXIS.axisLabel,
        interval: x.length > 40 ? 'auto' : 0,
      },
      name: 'turn', nameLocation: 'middle', nameGap: 26,
      nameTextStyle: { color: 'rgba(255,255,255,0.4)', fontFamily: 'Red Hat Text' },
    },
    yAxis: {
      ...Y_AXIS, type: 'value',
      axisLabel: {
        ...Y_AXIS.axisLabel,
        formatter: v => {
          const n = Math.abs(v);
          if (n >= 1e9) return (v / 1e9).toFixed(1) + 'B';
          if (n >= 1e6) return (v / 1e6).toFixed(1) + 'M';
          if (n >= 1e3) return (v / 1e3).toFixed(0) + 'K';
          return v;
        },
      },
    },
    series: [{
      type: 'line',
      data: values,
      smooth: true,
      showSymbol: false,
      lineStyle: { width: 2, color: '#19F58C' },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(25, 245, 140, 0.40)' },
            { offset: 1, color: 'rgba(25, 245, 140, 0)' },
          ],
        },
      },
      markPoint: markers.length ? {
        symbol: 'circle',
        symbolSize: 10,
        itemStyle: { borderColor: '#000', borderWidth: 2 },
        label: { show: false },
        data: markers.map(m => ({
          name: m.label || 'correction',
          xAxis: String(m.turn),
          yAxis: m.value ?? 0,
          itemStyle: { color: m.color || '#FF423D' },
        })),
      } : undefined,
      markLine: thresholds.length ? {
        symbol: ['none', 'none'],
        silent: true,
        lineStyle: { type: 'dashed', width: 1.5 },
        label: {
          position: 'insideEndTop',
          fontFamily: 'Red Hat Mono', fontSize: 10,
        },
        data: thresholds.map(t => ({
          yAxis: t.value,
          lineStyle: { color: t.color },
          label: { formatter: t.label || '', color: t.color },
        })),
      } : undefined,
    }],
  });
  return c;
}

export function donutChart(el, data) {
  const c = mount(el);
  c.setOption({
    backgroundColor: 'transparent',
    color: PALETTE,
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(15, 15, 15, 0.92)',
      borderColor: 'rgba(255, 255, 255, 0.12)',
      borderWidth: 1,
      textStyle: { color: '#FFFFFF', fontFamily: 'Red Hat Text' },
      extraCssText: 'backdrop-filter: blur(12px); border-radius: 12px;',
      formatter: p => `${p.name}<br/><b>${Number(p.value).toLocaleString()}</b> tokens (${p.percent.toFixed(1)}%)`,
    },
    legend: {
      textStyle: { color: TEXT_COLOR, fontFamily: 'Red Hat Text' },
      bottom: 8, icon: 'roundRect', itemWidth: 8, itemHeight: 8,
      type: 'scroll',
    },
    series: [{
      type: 'pie',
      center: ['50%', '46%'],
      radius: ['54%', '74%'],
      avoidLabelOverlap: true,
      padAngle: 2,
      itemStyle: {
        borderColor: '#000000',
        borderWidth: 2,
        borderRadius: 6,
      },
      label: {
        show: true,
        position: 'inside',
        color: '#000000',
        fontFamily: 'Red Hat Display',
        fontSize: 12,
        fontWeight: 500,
        formatter: ({ percent }) => percent >= 7 ? percent.toFixed(0) + '%' : '',
      },
      labelLine: { show: false },
      data,
    }],
  });
  return c;
}
