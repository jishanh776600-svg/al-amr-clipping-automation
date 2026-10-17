import React from 'react';
import {
    AbsoluteFill,
    interpolate,
    spring,
    staticFile,
    useCurrentFrame,
    useVideoConfig,
    Video,
    Audio,
} from 'remotion';

export interface ShortProps {
    videoSrc: string;
    headlineText: string;
    handle: string;
    brollSrc?: string;
    brollStartFrame?: number;
    brollDurationFrames?: number;
    whooshSfxSrc?: string;
    popSfxSrc?: string;
}

const resolveMediaSrc = (src?: string) => {
    if (!src) return '';
    if (src.startsWith('http://') || src.startsWith('https://') || src.startsWith('data:')) {
        return src;
    }
    const clean = src.startsWith('/') ? src.slice(1) : src;
    return staticFile(clean);
};

export const ContentRewardsShort: React.FC<ShortProps> = ({
    videoSrc,
    headlineText,
    handle,
    brollSrc,
    brollStartFrame = 240,
    brollDurationFrames = 75,
    whooshSfxSrc,
    popSfxSrc,
}) => {
    const frame = useCurrentFrame();
    const { fps, width, height } = useVideoConfig();

    const headlineProgress = spring({
        frame,
        fps,
        config: { damping: 14, mass: 0.6, stiffness: 100 },
    });
    const headlineY = interpolate(headlineProgress, [0, 1], [-200, 120]);
    const headlineOpacity = interpolate(headlineProgress, [0, 1], [0, 1]);

    const badgeProgress = spring({
        frame: frame - 20,
        fps,
        config: { damping: 12, mass: 0.5, stiffness: 120 },
    });
    const badgeY = interpolate(badgeProgress, [0, 1], [height + 100, 1680]);
    const badgeScale = interpolate(badgeProgress, [0, 1], [0.5, 1]);
    const badgeOpacity = interpolate(badgeProgress, [0, 1], [0, 1]);

    const words = (headlineText || 'WATCH THIS TILL END').toUpperCase().split(' ');
    const renderedWords = words.map((w, idx) => {
        let color = '#FFFFFF';
        if (idx === 0 || idx === 1) color = '#FFD700';
        else if (idx === words.length - 1 || idx === words.length - 2) color = '#00E5FF';
        return (
            <span key={idx} style={{ color, marginRight: 12, textShadow: '0 4px 16px rgba(0,0,0,0.9)' }}>
                {w}
            </span>
        );
    });

    const isBrollActive =
        Boolean(brollSrc) &&
        frame >= brollStartFrame &&
        frame < brollStartFrame + brollDurationFrames;

    const brollEntryProgress = spring({
        frame: frame - brollStartFrame,
        fps,
        config: { damping: 15, mass: 0.4, stiffness: 140 },
    });
    const brollX = interpolate(brollEntryProgress, [0, 1], [width, 0]);

    const resolvedVideo = resolveMediaSrc(videoSrc);
    const resolvedBroll = resolveMediaSrc(brollSrc);
    const resolvedPopSfx = resolveMediaSrc(popSfxSrc);
    const resolvedWhooshSfx = resolveMediaSrc(whooshSfxSrc);

    return (
        <AbsoluteFill style={{ backgroundColor: '#000000', overflow: 'hidden' }}>
            <div
                style={{
                    position: 'absolute',
                    top: 370,
                    left: 0,
                    width: 1080,
                    height: 1180,
                    overflow: 'hidden',
                    display: 'flex',
                    justifyContent: 'center',
                    alignItems: 'center',
                    backgroundColor: '#000000',
                }}
            >
                {resolvedVideo ? (
                    <Video
                        src={resolvedVideo}
                        style={{
                            width: '100%',
                            height: '100%',
                            objectFit: 'contain',
                        }}
                    />
                ) : null}

                {isBrollActive && resolvedBroll ? (
                    <div
                        style={{
                            position: 'absolute',
                            top: 0,
                            left: 0,
                            width: '100%',
                            height: '100%',
                            transform: `translateX(${brollX}px)`,
                            zIndex: 20,
                        }}
                    >
                        <Video
                            src={resolvedBroll}
                            style={{
                                width: '100%',
                                height: '100%',
                                objectFit: 'cover',
                            }}
                        />
                    </div>
                ) : null}
            </div>

            <div
                style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    width: 1080,
                    transform: `translateY(${headlineY}px)`,
                    opacity: headlineOpacity,
                    display: 'flex',
                    justifyContent: 'center',
                    padding: '0 60px',
                    boxSizing: 'border-box',
                    zIndex: 30,
                }}
            >
                <div
                    style={{
                        fontFamily: 'sans-serif',
                        fontWeight: 900,
                        fontSize: 48,
                        lineHeight: 1.25,
                        textAlign: 'center',
                        display: 'flex',
                        flexWrap: 'wrap',
                        justifyContent: 'center',
                    }}
                >
                    {renderedWords}
                </div>
            </div>

            <div
                style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    width: 1080,
                    transform: `translateY(${badgeY}px) scale(${badgeScale})`,
                    opacity: badgeOpacity,
                    display: 'flex',
                    justifyContent: 'center',
                    zIndex: 30,
                }}
            >
                <div
                    style={{
                        backgroundColor: '#FF0000',
                        borderRadius: 30,
                        padding: '12px 28px',
                        display: 'flex',
                        alignItems: 'center',
                        boxShadow: '0 8px 24px rgba(255, 0, 0, 0.45)',
                    }}
                >
                    <div
                        style={{
                            width: 0,
                            height: 0,
                            borderTop: '10px solid transparent',
                            borderBottom: '10px solid transparent',
                            borderLeft: '16px solid #FFFFFF',
                            marginRight: 14,
                        }}
                    />
                    <span
                        style={{
                            fontFamily: 'sans-serif',
                            fontWeight: 800,
                            fontSize: 26,
                            color: '#FFFFFF',
                            letterSpacing: 1.5,
                        }}
                    >
                        {handle || '@ALPHA_FUTURES'}
                    </span>
                </div>
            </div>

            {resolvedPopSfx && frame === 20 ? <Audio src={resolvedPopSfx} /> : null}
            {resolvedWhooshSfx && isBrollActive && frame === brollStartFrame ? (
                <Audio src={resolvedWhooshSfx} />
            ) : null}
        </AbsoluteFill>
    );
};
