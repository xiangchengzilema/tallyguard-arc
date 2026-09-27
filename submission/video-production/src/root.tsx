import React from 'react';
import {Composition} from 'remotion';
import {Film} from './Film';

export const Root: React.FC = () => <Composition
  id="TallyGuardFilm"
  component={Film}
  durationInFrames={195 * 30}
  fps={30}
  width={1920}
  height={1080}
  defaultProps={{withBgm: true}}
/>;
