import React from 'react';
import {Composition} from 'remotion';
import {Film} from './Film';

export const Root: React.FC = () => <>
  <Composition
    id="TallyGuardFilm"
    component={Film}
    durationInFrames={192 * 30}
    fps={30}
    width={1920}
    height={1080}
    defaultProps={{withBgm: true}}
  />
  <Composition
    id="TallyGuardFilmSubmit"
    component={Film}
    durationInFrames={179 * 30}
    fps={30}
    width={1920}
    height={1080}
    defaultProps={{withBgm: true, submissionCut: true}}
  />
</>;
