// カメラの動き（ショットファイルの camera.move、演出ノート P-03）を、拡大率と移動量（画面のピクセル）に直す。
// tools/animatic.py の zoompan と同じ動きになるように合わせてある。n はカットの頭からのフレーム、N はカットのフレーム数。
export type Cam = {z: number; x: number; y: number};

export function camera(move: string, n: number, N: number, W: number, H: number): Cam {
  const p = N > 1 ? Math.min(1, n / N) : 0;
  switch (move) {
    case 'push':
      return {z: 1 + 0.1 * p, x: 0, y: 0};
    case 'push_fast':
      return {z: Math.min(1 + (0.2 * n) / Math.max(1, Math.floor(N * 0.35)), 1.2), x: 0, y: 0};
    case 'pull':
      return {z: 1.12 - 0.12 * p, x: 0, y: 0};
    case 'pan_r':
    case 'pan_l': {
      const z = 1.15;
      const q = move === 'pan_r' ? p : 1 - p; // 見ている範囲の左端の位置（0 = 画の左端）
      return {z, x: -W * (z - 1) * (q - 0.5), y: 0};
    }
    case 'tilt_up':
    case 'tilt_down': {
      const z = 1.15;
      const q = move === 'tilt_down' ? p : 1 - p;
      return {z, x: 0, y: -H * (z - 1) * (q - 0.5)};
    }
    case 'shake': {
      const z = 1.08;
      const decay = Math.max(0, 1 - n / 14);
      // zoompan は2倍に拡大した画の上で 28px・20px 揺らしていた → 画面では z/2 倍
      return {z, x: -28 * Math.sin(n * 2.3) * decay * (z / 2), y: -20 * Math.cos(n * 2.9) * decay * (z / 2)};
    }
    default:
      return {z: 1, x: 0, y: 0};
  }
}
