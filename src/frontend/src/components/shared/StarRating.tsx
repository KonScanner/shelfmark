const STAR_POSITIONS = [0, 1, 2, 3, 4] as const;

const STAR_PATH =
  'M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z';

const SIZE_CLASS = {
  sm: 'h-3.5 w-3.5',
  md: 'h-4 w-4',
} as const;

interface StarRatingProps {
  rating: number;
  maxRating?: number;
  size?: keyof typeof SIZE_CLASS;
}

// 5-star rating display with partial fill support
export function StarRating({ rating, maxRating = 5, size = 'md' }: StarRatingProps) {
  // Normalize rating to 0-5 scale if needed
  const normalizedRating = Math.min(Math.max(rating, 0), maxRating);
  const sizeClass = SIZE_CLASS[size];

  return (
    <div className="flex items-center gap-0.5" title={`${rating} out of ${maxRating}`}>
      {STAR_POSITIONS.map((starPosition) => {
        const fillPercentage = Math.min(Math.max((normalizedRating - starPosition) * 100, 0), 100);

        return (
          <div key={starPosition} className={`relative ${sizeClass}`}>
            {/* Empty star (gray background) */}
            <svg
              className={`absolute inset-0 ${sizeClass} text-zinc-300 dark:text-zinc-600`}
              fill="currentColor"
              viewBox="0 0 20 20"
              aria-hidden="true"
            >
              <path d={STAR_PATH} />
            </svg>
            {/* Filled star (gold, clipped to fill percentage) */}
            <svg
              className={`absolute inset-0 ${sizeClass} text-amber-400`}
              fill="currentColor"
              viewBox="0 0 20 20"
              style={{ clipPath: `inset(0 ${100 - fillPercentage}% 0 0)` }}
              aria-hidden="true"
            >
              <path d={STAR_PATH} />
            </svg>
          </div>
        );
      })}
    </div>
  );
}
