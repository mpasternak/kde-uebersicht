import { css } from "uebersicht";

export const command = "date '+%H:%M:%S'; uname -sr";
export const refreshFrequency = 1000;

export const className = `
  top: 80px;
  left: 80px;
  padding: 12px 16px;
  border-radius: 12px;
  background: rgba(20, 20, 24, 0.75);
  color: #eee;
  font: 14px "Noto Sans", sans-serif;
`;

const big = css`font-size: 28px; font-weight: 700;`;

export const render = ({ output, error }) => {
  if (error) return <div>⚠ {error}</div>;
  const [time, kernel] = (output || "").trim().split("\n");
  return (
    <div>
      <div className={big}>{time}</div>
      <div>{kernel}</div>
      <a href="https://github.com/felixhageloh/uebersicht" style={{ color: "#8cf" }}>Übersicht API</a>
    </div>
  );
};
