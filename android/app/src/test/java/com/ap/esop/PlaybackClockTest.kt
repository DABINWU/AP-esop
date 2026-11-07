package com.ap.esop

import org.junit.Assert.*
import org.junit.Test

class PlaybackClockTest {
    @Test fun pauseResumeUsesRemainingTime(){var now=0L;val clock=PlaybackClock{now};clock.load(20000,false);now=7000;clock.pause();assertEquals(13000,clock.left());now=100000;assertEquals(13000,clock.left());clock.resume();now+=5000;assertEquals(8000,clock.left())}
    @Test fun manualNewItemKeepsPausedAtFullDuration(){var now=0L;val clock=PlaybackClock{now};clock.load(20000,false);now=5000;clock.pause();clock.load(30000,clock.paused);now=900000;assertTrue(clock.paused);assertEquals(30000,clock.left())}
    @Test fun newAssignmentResetsToPlaying(){val clock=PlaybackClock{0};clock.load(20000,true);clock.load(15000,false);assertFalse(clock.paused);assertEquals(15000,clock.left())}
    @Test fun timeNeverGoesNegative(){var now=0L;val clock=PlaybackClock{now};clock.load(1000,false);now=9000;assertEquals(0,clock.left())}
}
