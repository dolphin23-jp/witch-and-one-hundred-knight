import React from 'react';
import {AbsoluteFill, Audio, Img, Sequence, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {camera} from './camera';

// scene.json（tools/scene_export.py が作る）の形
export type Cut = {id: string; kind: string; start: number; sec: number; label: string; color: string; move: string;
  image: string | null; shot: string; acting: string};
export type Voice = {src: string; at: number; sec: number};
export type Sub = {start: number; end: number; text: string};
export type Overlay = {cut: string; src: string; x: number; y: number; size: number; start: number; sec: number; anim?: string};
export type Bgm = {src: string; from: number; volume: number; sec: number; loop: boolean};
export type SceneData = {scene: string; width: number; height: number; fps: number; total: number;
  cuts: Cut[]; voices: Voice[]; subs: Sub[]; overlays: Overlay[]; bgm?: Bgm | null};

const FONT = '"Noto Serif CJK JP", "Noto Serif CJK", serif';
const f = (sec: number, fps: number) => Math.round(sec * fps);

const Still: React.FC<{cut: Cut}> = ({cut}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const N = Math.max(1, f(cut.sec, fps));
  const c = camera(cut.move, frame, N, width, height);
  return (
    <AbsoluteFill style={{backgroundColor: cut.color}}>
      {cut.image ? (
        <Img src={staticFile(cut.image)} style={{width: '100%', height: '100%', objectFit: 'cover',
          transform: `translate(${c.x}px, ${c.y}px) scale(${c.z})`, transformOrigin: 'center'}} />
      ) : (
        <div style={{fontFamily: FONT, color: '#e8e8e8', fontSize: 29, padding: '100px 60px', lineHeight: 1.5}}>
          画: {cut.shot}<br /><br />動き: {cut.acting}
        </div>
      )}
      <div style={{position: 'absolute', left: 20, top: 18, fontFamily: FONT, fontSize: 22, color: 'white',
        background: cut.color, opacity: 0.92, padding: '2px 8px', whiteSpace: 'pre'}}>{cut.label}</div>
    </AbsoluteFill>
  );
};

// 漫符（D-027）: 生成した画に含めず、別の層として重ねる。pop_pulse = ぽんと出て、小さく脈打つ
const Mark: React.FC<{o: Overlay}> = ({o}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const pop = spring({frame, fps, config: {damping: 9, stiffness: 220}});
  const pulse = o.anim === 'pop_pulse' ? 1 + 0.06 * Math.sin((frame / fps) * Math.PI * 4) * Math.min(1, frame / 8) : 1;
  const size = width * o.size;
  return (
    <Img src={staticFile(o.src)} style={{position: 'absolute', width: size, height: size,
      left: width * o.x - size / 2, top: height * o.y - size / 2, transform: `scale(${pop * pulse})`}} />
  );
};

const Timecode: React.FC = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  const s = `${String(Math.floor(t / 60)).padStart(2, '0')}:${(t % 60).toFixed(2).padStart(5, '0')}`;
  return <div style={{position: 'absolute', right: 30, top: 26, fontFamily: FONT, fontSize: 26, color: 'rgba(255,255,255,0.8)'}}>{s}</div>;
};

export const Scene: React.FC<SceneData> = (d) => {
  const {fps} = useVideoConfig();
  return (
    <AbsoluteFill style={{backgroundColor: 'black'}}>
      {d.cuts.map((c) => (
        <Sequence key={c.id} from={f(c.start, fps)} durationInFrames={Math.max(1, f(c.start + c.sec, fps) - f(c.start, fps))} name={c.id}>
          <Still cut={c} />
        </Sequence>
      ))}
      {d.overlays.map((o, i) => (
        <Sequence key={`o${i}`} from={f(o.start, fps)} durationInFrames={f(o.sec, fps)} name={`漫符 ${o.cut}`}>
          <Mark o={o} />
        </Sequence>
      ))}
      {d.subs.map((s, i) => (
        <Sequence key={`s${i}`} from={f(s.start, fps)} durationInFrames={Math.max(1, f(s.end - s.start, fps))} name={`字幕 ${i}`}>
          <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 36}}>
            <div style={{fontFamily: FONT, fontSize: 30, color: 'white', maxWidth: 1100, textAlign: 'center', lineHeight: 1.35,
              WebkitTextStroke: '6px black', paintOrder: 'stroke fill', lineBreak: 'anywhere'}}>{s.text}</div>
          </AbsoluteFill>
        </Sequence>
      ))}
      {d.voices.map((v, i) => (
        <Sequence key={`v${i}`} from={f(v.at, fps)} durationInFrames={Math.max(1, Math.ceil(v.sec * fps) + 1)} name={`声 ${v.src}`}>
          <Audio src={staticFile(v.src)} />
        </Sequence>
      ))}
      {d.bgm ? (
        // BGM（D-045）: 原作音源から声を分けた残りをそのまま敷く。シーンより短ければループ
        <Audio src={staticFile(d.bgm.src)} volume={d.bgm.volume} loop={d.bgm.loop} startFrom={f(d.bgm.from, fps)} name="BGM" />
      ) : null}
      <Timecode />
    </AbsoluteFill>
  );
};

