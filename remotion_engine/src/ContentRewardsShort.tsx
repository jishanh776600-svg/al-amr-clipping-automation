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
    headlineText?: string;
    handle?: string;
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
    const clean = src.replace(/^\/?(public\/)?/, '');
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

    // Clean headline of non-printable or box glyphs
    const cleanHeadline = (headlineText || '')
        .replace(/[\u25a0-\u25ff\ufffd]/g, '')
        .replace(/\.{2,}$/, '')
        .trim();

    const headlineProgress = spring({
        frame,
        fps,
        config: { damping: 14, mass: 0.6, stiffness: 100 },
    });
    const headlineY = interpolate(headlineProgress, [0, 1], [-180, 140]);
    const headlineOpacity = interpolate(headlineProgress, [0, 1], [0, 1]);

    // Hook pill fades out smoothly after 3 seconds (90 frames)
    const hookFadeOut = interpolate(frame, [85, 105], [1, 0], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
    });
    const finalHeadlineOpacity = headlineOpacity * hookFadeOut;

    const badgeProgress = spring({
        frame: frame - 20,
        fps,
        config: { damping: 12, mass: 0.5, stiffness: 120 },
    });
    const badgeY = interpolate(badgeProgress, [0, 1], [height + 100, 1720]);
    const badgeScale = interpolate(badgeProgress, [0, 1], [0.5, 1]);
    const badgeOpacity = interpolate(badgeProgress, [0, 1], [0, 1]);

    const words = cleanHeadline.toUpperCase().split(' ').filter(Boolean);
    const renderedWords = words.map((w, idx) => {
        let color = '#FFFFFF';
        if (idx === 0 || idx === 1) color = '#FFD700';
        else if (idx === words.length - 1 || idx === words.length - 2) color = '#00E5FF';
        return (
            <span key={idx} style={{ color, marginRight: 10, textShadow: '0 2px 10px rgba(0,0,0,0.8)' }}>
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
            {/* Full-bleed 1080x1920 video canvas (zero black bars) */}
            <div
                style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    width: 1080,
                    height: 1920,
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
                            objectFit: 'cover',
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

            {/* Opening hook badge (auto-fades after 3 seconds) */}
            {cleanHeadline && finalHeadlineOpacity > 0.01 ? (
                <div
                    style={{
                        position: 'absolute',
                        top: 0,
                        left: 0,
                        width: 1080,
                        transform: `translateY(${headlineY}px)`,
                        opacity: finalHeadlineOpacity,
                        display: 'flex',
                        justifyContent: 'center',
                        padding: '0 40px',
                        boxSizing: 'border-box',
                        zIndex: 30,
                    }}
                >
                    <div
                        style={{
                            backgroundColor: 'rgba(0, 0, 0, 0.78)',
                            backdropFilter: 'blur(12px)',
                            border: '1px solid rgba(255, 255, 255, 0.2)',
                            borderRadius: 24,
                            padding: '16px 32px',
                            fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                            fontWeight: 900,
                            fontSize: 40,
                            lineHeight: 1.25,
                            textAlign: 'center',
                            display: 'flex',
                            flexWrap: 'wrap',
                            justifyContent: 'center',
                            boxShadow: '0 12px 32px rgba(0, 0, 0, 0.65)',
                        }}
                    >
                        {renderedWords}
                    </div>
                </div>
            ) : null}

            {/* Bottom attribution badge (ONLY if handle is explicitly provided) */}
            {handle && handle.trim() ? (
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
                            backgroundColor: 'rgba(230, 0, 0, 0.9)',
                            borderRadius: 30,
                            padding: '10px 24px',
                            display: 'flex',
                            alignItems: 'center',
                            boxShadow: '0 8px 24px rgba(0, 0, 0, 0.45)',
                        }}
                    >
                        <div
                            style={{
                                width: 0,
                                height: 0,
                                borderTop: '8px solid transparent',
                                borderBottom: '8px solid transparent',
                                borderLeft: '14px solid #FFFFFF',
                                marginRight: 12,
                            }}
                        />
                        <span
                            style={{
                                fontFamily: 'system-ui, -apple-system, sans-serif',
                                fontWeight: 800,
                                fontSize: 24,
                                color: '#FFFFFF',
                                letterSpacing: 1.2,
                            }}
                        >
                            {handle.trim()}
                        </span>
                    </div>
                </div>
            ) : null}

            {resolvedPopSfx && frame === 20 ? <Audio src={resolvedPopSfx} /> : null}
            {resolvedWhooshSfx && isBrollActive && frame === brollStartFrame ? (
                <Audio src={resolvedWhooshSfx} />
            ) : null}
        </AbsoluteFill>
    );
};
