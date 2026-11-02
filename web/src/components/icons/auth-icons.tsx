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

export function AlertErrorIcon({ className = "w-[18px] h-[18px]" }: { className?: string }) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 18 18"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
    >
      <rect width="18" height="18" rx="9" fill="#FF4D4D" fillOpacity="0.22" />
      <rect x="0.5" y="0.5" width="17" height="17" rx="8.5" stroke="#FF5C5C" strokeOpacity="0.8" />
      <path
        d="M9.41122 5.72727L9.27273 10.8196H7.97301L7.83097 5.72727H9.41122ZM8.62287 13.0923C8.38849 13.0923 8.18726 13.0095 8.01918 12.8438C7.85109 12.6757 7.76823 12.4744 7.7706 12.2401C7.76823 12.008 7.85109 11.8092 8.01918 11.6435C8.18726 11.4777 8.38849 11.3949 8.62287 11.3949C8.84777 11.3949 9.04545 11.4777 9.21591 11.6435C9.38636 11.8092 9.47277 12.008 9.47514 12.2401C9.47277 12.3963 9.43134 12.5395 9.35085 12.6697C9.27273 12.7976 9.16974 12.9006 9.0419 12.9787C8.91406 13.0545 8.77438 13.0923 8.62287 13.0923Z"
        fill="#FF7878"
      />
    </svg>
  );
}

export function SecurityLockMintIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg
      className={className}
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        d="M4 6.66658V5.33325C4 5.10611 4.01893 4.88339 4.05531 4.66658M12 6.66658V5.33325C12 3.12411 10.2091 1.33325 8 1.33325C6.80533 1.33325 5.73295 1.85701 5 2.68743"
        strokeLinecap="round"
        stroke="#00F5A0"
      />
      <path
        d="M7.33301 14.6667H5.33301C3.44739 14.6667 2.50458 14.6667 1.91879 14.0809C1.33301 13.4951 1.33301 12.5523 1.33301 10.6667C1.33301 8.78115 1.33301 7.83835 1.91879 7.25255C2.50458 6.66675 3.44739 6.66675 5.33301 6.66675H10.6663C12.5519 6.66675 13.4947 6.66675 14.0805 7.25255C14.6663 7.83835 14.6663 8.78115 14.6663 10.6667V10.6667V10.6668C14.6663 12.5524 14.6663 13.4951 14.0805 14.0809C13.4947 14.6667 12.5519 14.6667 10.6663 14.6667H9.99967"
        strokeLinecap="round"
        stroke="#00F5A0"
      />
    </svg>
  );
}

export function ArrowRightMintIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg
      className={className}
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        d="M9 3L14 8M14 8L9 13M14 8H2"
        strokeWidth="1.66667"
        strokeLinecap="round"
        strokeLinejoin="round"
        stroke="#006B43"
      />
    </svg>
  );
}
