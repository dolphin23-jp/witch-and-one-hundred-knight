import React from 'react';
import {Composition} from 'remotion';
import {Scene, SceneData} from './Scene';

// scene.json は render の --props で渡す。尺と大きさも scene.json から決める
const empty: SceneData = {scene: 'A01', width: 1280, height: 720, fps: 24, total: 1, cuts: [], voices: [], subs: [], overlays: []};

export const Root: React.FC = () => (
  <Composition id="Scene" component={Scene} defaultProps={empty} durationInFrames={24} fps={24} width={1280} height={720}
    calculateMetadata={({props}) => ({durationInFrames: Math.round(props.total * props.fps), fps: props.fps,
      width: props.width, height: props.height})} />
);
