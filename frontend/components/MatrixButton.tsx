import React, { ButtonHTMLAttributes, CSSProperties } from 'react';

interface MatrixButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  backgroundColor?: string;
  textColor?: string;
  size?: 'small' | 'medium' | 'large';
}

const MatrixButton: React.FC<MatrixButtonProps> = ({
  children,
  backgroundColor = 'hsl(var(--matrix))',
  textColor = 'hsl(var(--matrix-bg))',
  size = 'medium',
  disabled,
  style,
  ...props
}) => {
  let padding: string;
  let fontSize: string;

  switch (size) {
    case 'small':
      padding = '0.2rem 0.5rem';
      fontSize = '0.8rem';
      break;
    case 'large':
      padding = '0.8rem 1.5rem';
      fontSize = '1.2rem';
      break;
    default:
      padding = '0.5rem 1rem';
      fontSize = '1rem';
      break;
  }

  const buttonStyle: CSSProperties = {
    backgroundColor: backgroundColor,
    color: textColor,
    padding: padding,
    fontSize: fontSize,
    border: '1px solid hsl(var(--matrix-border-color))',
    borderRadius: '4px',
    cursor: disabled ? 'not-allowed' : 'pointer',
    fontFamily: 'var(--font-mono)',
    textTransform: 'uppercase',
    fontWeight: '300',
    transition: 'background-color 0.2s, border-color 0.2s',
    ...style,
  };

  const hoverStyle: CSSProperties = {
    backgroundColor: `hsl(var(--matrix-light))`,
    borderColor: `hsl(var(--matrix-light))`,
  };

  const focusStyle: CSSProperties = {
    outline: 'none',
    boxShadow: `0 0 0 2px hsl(var(--matrix-light))`,
  };

  const disabledStyle: CSSProperties = {
    backgroundColor: `hsl(var(--matrix-muted-text))`,
    borderColor: `hsl(var(--matrix-muted-text))`,
    color: `hsl(var(--matrix-dark))`,
    cursor: 'not-allowed',
  };

  return (
    <button
      {...props}
      disabled={disabled}
      style={{
        ...buttonStyle,
        ...(disabled ? disabledStyle : {}),
      }}
      onMouseEnter={(e) => {
        if (!disabled) {
          Object.assign(e.currentTarget.style, hoverStyle);
        }
      }}
      onMouseLeave={(e) => {
        if (!disabled) {
          Object.assign(e.currentTarget.style, {
            backgroundColor: buttonStyle.backgroundColor,
            borderColor: buttonStyle.borderColor,
          });
        }
      }}
      onFocus={(e) => {
        if (!disabled) {
          Object.assign(e.currentTarget.style, focusStyle);
        }
      }}
      onBlur={(e) => {
        if (!disabled) {
          e.currentTarget.style.boxShadow = 'none';
        }
      }}
    >
      {children}
    </button>
  );
};

export default MatrixButton;