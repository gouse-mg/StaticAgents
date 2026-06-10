class TargetCursor {
  constructor(options = {}) {
    this.targetSelector = options.targetSelector || '.cursor-target';
    this.spinDuration = options.spinDuration || 2;
    this.hideDefaultCursor = options.hideDefaultCursor !== undefined ? options.hideDefaultCursor : true;
    this.hoverDuration = options.hoverDuration || 0.2;
    this.parallaxOn = options.parallaxOn !== undefined ? options.parallaxOn : true;

    this.cursor = null;
    this.corners = [];
    this.dot = null;
    this.spinTl = null;
    
    this.activeTarget = null;
    this.activeStrength = { current: 0 };
    this.targetCornerPositions = null;
    
    this.isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || window.innerWidth <= 768;
    
    this.constants = {
      borderWidth: 3,
      cornerSize: 12
    };

    if (!this.isMobile) {
      this.init();
    }
  }

  init() {
    this.createElements();
    this.setupGSAP();
    this.addEventListeners();
    this.startSpinning();
  }

  createElements() {
    this.cursor = document.createElement('div');
    this.cursor.className = 'target-cursor-wrapper';
    this.cursor.innerHTML = `
      <div class="target-cursor-dot"></div>
      <div class="target-cursor-corner corner-tl"></div>
      <div class="target-cursor-corner corner-tr"></div>
      <div class="target-cursor-corner corner-br"></div>
      <div class="target-cursor-corner corner-bl"></div>
    `;
    document.body.appendChild(this.cursor);
    this.cursor.style.display = 'block';

    this.corners = Array.from(this.cursor.querySelectorAll('.target-cursor-corner'));
    this.dot = this.cursor.querySelector('.target-cursor-dot');

    if (this.hideDefaultCursor) {
      document.body.style.cursor = 'none';
      // Also apply to all existing targets
      document.querySelectorAll(this.targetSelector).forEach(el => el.style.cursor = 'none');
    }
  }

  setupGSAP() {
    gsap.set(this.cursor, {
      xPercent: -50,
      yPercent: -50,
      x: window.innerWidth / 2,
      y: window.innerHeight / 2
    });
  }

  startSpinning() {
    if (this.spinTl) this.spinTl.kill();
    this.spinTl = gsap.timeline({ repeat: -1 })
      .to(this.cursor, { rotation: '+=360', duration: this.spinDuration, ease: 'none' });
  }

  addEventListeners() {
    window.addEventListener('mousemove', (e) => {
      gsap.to(this.cursor, {
        x: e.clientX,
        y: e.clientY,
        duration: 0.1,
        ease: 'power3.out'
      });
    });

    window.addEventListener('mousedown', () => {
      gsap.to(this.dot, { scale: 0.7, duration: 0.3 });
      gsap.to(this.cursor, { scale: 0.9, duration: 0.2 });
    });

    window.addEventListener('mouseup', () => {
      gsap.to(this.dot, { scale: 1, duration: 0.3 });
      gsap.to(this.cursor, { scale: 1, duration: 0.2 });
    });

    window.addEventListener('mouseover', (e) => {
      const target = e.target.closest(this.targetSelector);
      if (target && target !== this.activeTarget) {
        this.handleEnter(target);
      }
    });

    // Handle dynamically added elements
    const observer = new MutationObserver((mutations) => {
      if (this.hideDefaultCursor) {
        document.querySelectorAll(this.targetSelector).forEach(el => el.style.cursor = 'none');
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  handleEnter(target) {
    this.activeTarget = target;
    
    // Stop spinning
    if (this.spinTl) this.spinTl.pause();
    gsap.to(this.cursor, { rotation: 0, duration: 0.2 });

    const rect = target.getBoundingClientRect();
    const { borderWidth, cornerSize } = this.constants;
    
    this.targetCornerPositions = [
      { x: rect.left - borderWidth, y: rect.top - borderWidth },
      { x: rect.right + borderWidth - cornerSize, y: rect.top - borderWidth },
      { x: rect.right + borderWidth - cornerSize, y: rect.bottom + borderWidth - cornerSize },
      { x: rect.left - borderWidth, y: rect.bottom + borderWidth - cornerSize }
    ];

    gsap.ticker.add(this.tick.bind(this));
    
    gsap.to(this.activeStrength, {
      current: 1,
      duration: this.hoverDuration,
      ease: 'power2.out'
    });

    const leaveHandler = () => {
      this.handleLeave();
      target.removeEventListener('mouseleave', leaveHandler);
    };
    target.addEventListener('mouseleave', leaveHandler);
  }

  handleLeave() {
    gsap.ticker.remove(this.tick.bind(this));
    this.activeStrength.current = 0;
    this.activeTarget = null;
    this.targetCornerPositions = null;

    // Reset corners
    const { cornerSize } = this.constants;
    const positions = [
      { x: -cornerSize * 1.5, y: -cornerSize * 1.5 },
      { x: cornerSize * 0.5, y: -cornerSize * 1.5 },
      { x: cornerSize * 0.5, y: cornerSize * 0.5 },
      { x: -cornerSize * 1.5, y: cornerSize * 0.5 }
    ];

    this.corners.forEach((corner, i) => {
      gsap.to(corner, {
        x: positions[i].x,
        y: positions[i].y,
        duration: 0.3,
        ease: 'power3.out'
      });
    });

    // Resume spinning
    if (this.spinTl) this.spinTl.resume();
  }

  tick() {
    if (!this.targetCornerPositions || !this.activeTarget) return;

    const strength = this.activeStrength.current;
    const cursorX = gsap.getProperty(this.cursor, 'x');
    const cursorY = gsap.getProperty(this.cursor, 'y');

    this.corners.forEach((corner, i) => {
      const currentX = gsap.getProperty(corner, 'x');
      const currentY = gsap.getProperty(corner, 'y');

      const targetX = this.targetCornerPositions[i].x - cursorX;
      const targetY = this.targetCornerPositions[i].y - cursorY;

      const finalX = currentX + (targetX - currentX) * strength;
      const finalY = currentY + (targetY - currentY) * strength;

      gsap.set(corner, { x: finalX, y: finalY });
    });
  }
}
