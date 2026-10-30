import React from "react";

export function SsoShieldIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 16 16"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <g clipPath="url(#sso-shield-clip)">
        <path
          d="M8 0.666504L2 3.33317V7.33317C2 11.0332 4.56 14.4932 8 15.3332C11.44 14.4932 14 11.0332 14 7.33317V3.33317L8 0.666504ZM7.33333 4.6665H8.66667V5.99984H7.33333V4.6665ZM7.33333 7.33317H8.66667V11.3332H7.33333V7.33317Z"
          fill="#006B43"
        />
      </g>
      <defs>
        <clipPath id="sso-shield-clip">
          <rect width="16" height="16" fill="white" />
        </clipPath>
      </defs>
    </svg>
  );
}

export function SecurityLockIcon({ className = "w-6 h-6" }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 240 240"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        d="M60 100V80C60 76.5929 60.284 73.2521 60.8296 70M180 100V80C180 46.8629 153.137 20 120 20C102.08 20 85.9942 27.8563 75 40.3126"
        strokeLinecap="round"
        stroke="currentColor"
        strokeWidth="12"
      />
      <path
        d="M110 220H80C51.7157 220 37.5736 220 28.7868 211.213C20 202.426 20 188.284 20 160C20 131.716 20 117.574 28.7868 108.787C37.5736 100 51.7157 100 80 100H160C188.284 100 202.426 100 211.213 108.787C220 117.574 220 131.716 220 160V160V160C220 188.284 220 202.426 211.213 211.213C202.426 220 188.284 220 160 220H150"
        strokeLinecap="round"
        stroke="currentColor"
        strokeWidth="12"
      />
    </svg>
  );
}
