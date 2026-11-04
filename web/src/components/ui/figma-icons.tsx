import React from "react";

interface IconProps extends React.SVGProps<SVGSVGElement> {
  className?: string;
}

// 1. Overview / Home
export function FigmaOverviewIcon({ className, ...props }: IconProps) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <path
        d="M1.75 7L2.91667 5.83333M2.91667 5.83333L7 1.75L11.0833 5.83333M2.91667 5.83333V11.6667C2.91667 11.9886 3.17805 12.25 3.5 12.25H5.25M11.0833 5.83333L12.25 7M11.0833 5.83333V11.6667C11.0833 11.9886 10.822 12.25 10.5 12.25H8.75M5.25 12.25C5.57195 12.25 5.83333 11.9886 5.83333 11.6667V9.33333C5.83333 9.01138 6.09472 8.75 6.41667 8.75H7.58333C7.90528 8.75 8.16667 9.01138 8.16667 9.33333V11.6667C8.16667 11.9886 8.42805 12.25 8.75 12.25M5.25 12.25H8.75"
        stroke="currentColor"
        strokeWidth="1.02083"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// 2. Projects (Folder)
export function FigmaProjectsIcon({ className, ...props }: IconProps) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <path
        d="M1.75 4.08341V9.91675C1.75 10.5606 2.27277 11.0834 2.91667 11.0834H11.0833C11.7272 11.0834 12.25 10.5606 12.25 9.91675V5.25008C12.25 4.60618 11.7272 4.08341 11.0833 4.08341H7.58333L6.41667 2.91675H2.91667C2.27277 2.91675 1.75 3.43951 1.75 4.08341V4.08341"
        stroke="currentColor"
        strokeWidth="1.02083"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// 3. Delivery Analytics / DORA (Bar Chart)
export function FigmaDoraIcon({ className, ...props }: IconProps) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <path
        d="M5.25 11.0833V7.58333C5.25 6.93943 4.72723 6.41667 4.08333 6.41667H2.91667C2.27277 6.41667 1.75 6.93943 1.75 7.58333V11.0833C1.75 11.7272 2.27277 12.25 2.91667 12.25H4.08333C4.72723 12.25 5.25 11.7272 5.25 11.0833V11.0833M5.25 11.0833V5.25C5.25 4.6061 5.77277 4.08333 6.41667 4.08333H7.58333C8.22723 4.08333 8.75 4.6061 8.75 5.25V11.0833M5.25 11.0833C5.25 11.7272 5.77277 12.25 6.41667 12.25H7.58333C8.22723 12.25 8.75 11.7272 8.75 11.0833M8.75 11.0833V2.91667C8.75 2.27277 9.27277 1.75 9.91667 1.75H11.0833C11.7272 1.75 12.25 2.27277 12.25 2.91667V11.0833C12.25 11.7272 11.7272 12.25 11.0833 12.25H9.91667C9.27277 12.25 8.75 11.7272 8.75 11.0833H5.25"
        stroke="currentColor"
        strokeWidth="1.02083"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// 4. Risks & Alerts (Triangle Warning)
export function FigmaAlertsIcon({ className, ...props }: IconProps) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <path
        d="M6.99983 5.2501V6.41676M6.99983 8.7501H7.00567M2.9585 11.0834H11.0412C11.9395 11.0834 12.5007 10.111 12.0515 9.33343L8.01017 2.33343C7.561 1.55585 6.43867 1.55585 5.9895 2.33343L1.94817 9.33343C1.499 10.111 2.06017 11.0834 2.9585 11.0834L6.99983 5.2501"
        stroke="currentColor"
        strokeWidth="1.02083"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// 5. Sprint & WIP Monitoring (Lightning Bolt)
export function FigmaSprintIcon({ className, ...props }: IconProps) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <path
        d="M7.58301 5.83333V1.75L2.33301 8.16667H6.41634V12.25L11.6663 5.83333H7.58301V5.83333"
        stroke="currentColor"
        strokeWidth="1.02083"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// 6. Scope Creep (Trend Up)
export function FigmaScopeCreepIcon({ className, ...props }: IconProps) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <path
        d="M7.58333 4.08325H12.25M12.25 4.08325V8.74992M12.25 4.08325L7.58333 8.74992L5.25 6.41659L1.75 9.91658"
        stroke="currentColor"
        strokeWidth="1.02083"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// 7. ADR Knowledge Base (Open Book)
export function FigmaAdrIcon({ className, ...props }: IconProps) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <path
        d="M7 3.64766V11.231M7 3.64766C6.31867 3.195 5.3935 2.91675 4.375 2.91675C3.3565 2.91675 2.43133 3.195 1.75 3.64766V11.231C2.43133 10.7783 3.3565 10.5001 4.375 10.5001C5.3935 10.5001 6.31867 10.7783 7 11.231M7 3.64766C7.68133 3.195 8.6065 2.91675 9.625 2.91675C10.6441 2.91675 11.5687 3.195 12.25 3.64766V11.231C11.5687 10.7783 10.6441 10.5001 9.625 10.5001C8.6065 10.5001 7.68133 10.7783 7 11.231"
        stroke="currentColor"
        strokeWidth="1.02083"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// 8. Semantic Search (Magnifying Glass)
export function FigmaSemanticSearchIcon({ className, ...props }: IconProps) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <path
        d="M12.25 12.25L8.75 8.75M9.91667 5.83333C9.91667 8.08699 8.08699 9.91667 5.83333 9.91667C3.57968 9.91667 1.75 8.08699 1.75 5.83333C1.75 3.57968 3.57968 1.75 5.83333 1.75C8.08699 1.75 9.91667 3.57968 9.91667 5.83333L12.25 12.25"
        stroke="currentColor"
        strokeWidth="1.02083"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// 9. Refactoring & Verification / ADR Governance (Shield Check)
export function FigmaVerificationIcon({ className, ...props }: IconProps) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <path
        d="M5.25 6.99995L6.41667 8.16662L8.75 5.83328M12.0272 3.49062C10.1827 3.58857 8.37476 2.95081 7 1.71729C5.62524 2.95081 3.81728 3.58857 1.97284 3.49062C1.8244 4.06527 1.74952 4.65644 1.75 5.24995C1.75 8.51137 3.98067 11.2525 7 12.0295C10.0193 11.2525 12.25 8.51195 12.25 5.24995C12.25 4.64212 12.1724 4.05295 12.0272 3.49062L5.25 6.99995"
        stroke="currentColor"
        strokeWidth="1.02083"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// 10. Workspace Settings (Gear)
export function FigmaSettingsIcon({ className, ...props }: IconProps) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <path
        d="M6.02292 2.51825C6.27142 1.49392 7.72858 1.49392 7.97708 2.51825C8.05271 2.83037 8.27285 3.0876 8.56955 3.21052C8.86624 3.33344 9.2038 3.30727 9.478 3.14008C10.3781 2.59175 11.4088 3.62192 10.8605 4.52258C10.6936 4.79666 10.6674 5.13396 10.7902 5.43047C10.913 5.72698 11.1699 5.94708 11.4817 6.02292C12.5061 6.27142 12.5061 7.72858 11.4817 7.97708C11.1696 8.05271 10.9124 8.27285 10.7895 8.56955C10.6666 8.86624 10.6927 9.2038 10.8599 9.478C11.4082 10.3781 10.3781 11.4088 9.47742 10.8605C9.20334 10.6936 8.86604 10.6674 8.56953 10.7902C8.27302 10.913 8.05292 11.1699 7.97708 11.4817C7.72858 12.5061 6.27142 12.5061 6.02292 11.4817C5.94729 11.1696 5.72715 10.9124 5.43045 10.7895C5.13376 10.6666 4.7962 10.6927 4.522 10.8599C3.62192 11.4082 2.59117 10.3781 3.1395 9.47742C3.30644 9.20334 3.33255 8.86604 3.20978 8.56953C3.087 8.27302 2.83008 8.05292 2.51825 7.97708C1.49392 7.72858 1.49392 6.27142 2.51825 6.02292C2.83037 5.94729 3.0876 5.72715 3.21052 5.43045C3.33344 5.13376 3.30727 4.7962 3.14008 4.522C2.59175 3.62192 3.62192 2.59117 4.52258 3.1395C5.10358 3.49417 5.86192 3.18033 6.02292 2.51825V2.51825"
        stroke="currentColor"
        strokeWidth="1.02083"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M5.25 7C5.25 7.96585 6.03415 8.75 7 8.75C7.96585 8.75 8.75 7.96585 8.75 7C8.75 6.03415 7.96585 5.25 7 5.25C6.03415 5.25 5.25 6.03415 5.25 7V7"
        stroke="currentColor"
        strokeWidth="1.02083"
      />
    </svg>
  );
}

// 11. Integrations (Puzzle Piece)
export function FigmaIntegrationsIcon({ className, ...props }: IconProps) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <path
        d="M6.41699 2.33341C6.41699 1.68951 6.93976 1.16675 7.58366 1.16675C8.22756 1.16675 8.75033 1.68951 8.75033 2.33341V2.91675C8.75033 3.23891 9.01149 3.50008 9.33366 3.50008H11.0837C11.4056 3.50008 11.667 3.76146 11.667 4.08341V5.83341C11.667 6.15536 11.4056 6.41675 11.0837 6.41675H10.5003C9.85642 6.41675 9.33366 6.93951 9.33366 7.58341C9.33366 8.22732 9.85642 8.75008 10.5003 8.75008H11.0837C11.4056 8.75008 11.667 9.01146 11.667 9.33341V11.0834C11.667 11.4054 11.4056 11.6667 11.0837 11.6667H9.33366C9.01171 11.6667 8.75033 11.4054 8.75033 11.0834V10.5001C8.75033 9.85618 8.22756 9.33341 7.58366 9.33341C6.93976 9.33341 6.41699 9.85618 6.41699 10.5001V11.0834C6.41699 11.4054 6.15561 11.6667 5.83366 11.6667H4.08366C3.76171 11.6667 3.50033 11.4054 3.50033 11.0834V9.33341C3.50033 9.01146 3.23894 8.75008 2.91699 8.75008H2.33366C1.68976 8.75008 1.16699 8.22732 1.16699 7.58341C1.16699 6.93951 1.68976 6.41675 2.33366 6.41675H2.91699C3.23916 6.41675 3.50033 6.15558 3.50033 5.83341V4.08341C3.50033 3.76146 3.76171 3.50008 4.08366 3.50008H5.83366C6.15561 3.50008 6.41699 3.2387 6.41699 2.91675V2.33341V2.33341"
        stroke="currentColor"
        strokeWidth="1.02083"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// 12. Access & Roles / Members (Users)
export function FigmaMembersIcon({ className, ...props }: IconProps) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <g clipPath="url(#figma_clip_members)">
        <path
          d="M9.91699 11.6666H12.8337V10.4999C12.8336 9.75443 12.3613 9.09082 11.6569 8.84661C10.9526 8.6024 10.1708 8.83121 9.70932 9.41667M9.91699 11.6666H4.08366M9.91699 11.6666V10.4999C9.91699 10.1173 9.84349 9.7515 9.70932 9.41667M4.08366 11.6666H1.16699V10.4999C1.16705 9.75443 1.63937 9.09082 2.34372 8.84661C3.04808 8.6024 3.82981 8.83121 4.29133 9.41667M4.08366 11.6666V10.4999C4.08366 10.1173 4.15716 9.7515 4.29133 9.41667M4.29133 9.41667C4.73453 8.30907 5.80735 7.58284 7.00033 7.58284C8.1933 7.58284 9.26612 8.30907 9.70932 9.41667M8.75033 4.08325C8.75033 5.0491 7.96618 5.83325 7.00033 5.83325C6.03447 5.83325 5.25033 5.0491 5.25033 4.08325C5.25033 3.1174 6.03447 2.33325 7.00033 2.33325C7.96618 2.33325 8.75033 3.1174 8.75033 4.08325L9.91699 11.6666M13.417 13.4166C13.417 14.0605 12.8942 14.5833 12.2503 14.5833C11.6064 14.5833 11.0837 14.0605 11.0837 13.4166C11.0837 12.7727 11.6064 12.2499 12.2503 12.2499C12.8942 12.2499 13.417 12.7727 13.417 13.4166V13.4166M4.08366 5.83325C4.08366 6.47715 3.56089 6.99992 2.91699 6.99992C2.27309 6.99992 1.75033 6.47715 1.75033 5.83325C1.75033 5.18935 2.27309 4.66659 2.91699 4.66659C3.56089 4.66659 4.08366 5.18935 4.08366 5.83325V5.83325"
          stroke="currentColor"
          strokeWidth="1.02083"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </g>
      <defs>
        <clipPath id="figma_clip_members">
          <rect width="14" height="14" fill="white" />
        </clipPath>
      </defs>
    </svg>
  );
}

// 13. Privacy & Erasure / Dead Letters (Trash Can)
export function FigmaTrashIcon({ className, ...props }: IconProps) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <path
        d="M11.083 4.08333L10.5773 11.1662C10.5338 11.7768 10.0257 12.25 9.41351 12.25H4.58584C3.97364 12.25 3.46556 11.7768 3.42209 11.1662L2.91634 4.08333M5.83301 6.41667V9.91667M8.16634 6.41667V9.91667M8.74967 4.08333V2.33333C8.74967 2.01138 8.48829 1.75 8.16634 1.75H5.83301C5.51106 1.75 5.24967 2.01138 5.24967 2.33333V4.08333M2.33301 4.08333H11.6663"
        stroke="currentColor"
        strokeWidth="1.02083"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// 14. Active Navigation Indicator Chevron (Width 5, Height 9, #00E599)
export function FigmaActiveChevron({ className, ...props }: IconProps) {
  return (
    <svg
      width="5"
      height="9"
      viewBox="0 0 5 9"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <path
        d="M4.13576 4.49892L3.45376 5.23592L0.307762 8.05192L-0.00023824 7.74392L2.55176 4.88392C1.47376 4.26059 0.681762 3.78026 0.175762 3.44292L0.857762 2.80492L4.01476 -7.64132e-05L4.31176 0.296924L1.79276 3.13492L4.13576 4.49892Z"
        fill="#00E599"
      />
    </svg>
  );
}

// 15. M.A.N.T.A. Small Emblem Logo Mark (Width 12, Height 12)
export function FigmaMantaEmblem({ className, ...props }: IconProps) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <path
        d="M11.2617 5.625L5.63672 11.2383L0 5.625L5.63672 0L11.2617 5.625V5.625M10.6641 5.625L5.63672 0.597656L0.597656 5.625L5.63672 10.6406L10.6641 5.625V5.625M9.33984 5.625L5.63672 9.33984L1.92188 5.625L5.63672 1.92188L9.33984 5.625V5.625"
        fill="currentColor"
      />
    </svg>
  );
}
