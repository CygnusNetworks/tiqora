/** 3-5 "nice" gridline values from 0 to a rounded maximum. */
export function niceTicks(max: number, count = 4): number[] {
  const top = max > 0 ? max : 1;
  const rough = top / count;
  const pow = 10 ** Math.floor(Math.log10(rough));
  const norm = rough / pow;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * pow;
  const ticks: number[] = [0];
  for (let i = 1; (i - 1) * step < top; i++) ticks.push(Number((i * step).toPrecision(12)));
  return ticks;
}

/** Bar path with the value end (top) rounded by rx/ry; the base stays square. */
export function barPath(x: number, y: number, w: number, h: number, round: boolean): string {
  if (!round || h <= 0) return `M${x},${y}H${x + w}V${y + h}H${x}Z`;
  const ry = Math.min(3, h);
  const rx = Math.min(0.8, w / 2);
  return (
    `M${x},${y + ry}A${rx},${ry} 0 0 1 ${x + rx},${y}H${x + w - rx}` +
    `A${rx},${ry} 0 0 1 ${x + w},${y + ry}V${y + h}H${x}Z`
  );
}
