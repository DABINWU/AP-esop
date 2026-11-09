package com.ap.esop

import org.junit.Assert.assertEquals
import org.junit.Test

class CommunicationHealthTest {
    @Test fun restoredContentDoesNotImplyCommunication() {assertEquals("unknown",CommunicationHealth.state(false,false,false,0,0,100000,40))}
    @Test fun syncAloneWaitsForReportConfirmation() {assertEquals("pending",CommunicationHealth.state(true,false,false,100000,0,100100,40))}
    @Test fun bothDirectionsMustBeFresh() {
        assertEquals("online",CommunicationHealth.state(true,false,false,100000,100000,110000,40))
        assertEquals("offline",CommunicationHealth.state(true,false,false,100000,110000,140001,40))
        assertEquals("offline",CommunicationHealth.state(true,false,false,110000,100000,140001,40))
    }
    @Test fun failedRequestTurnsRedWithoutStoppingPlayback() {
        assertEquals("offline",CommunicationHealth.state(false,true,false,100000,100000,100001,40))
        assertEquals("offline",CommunicationHealth.state(true,false,true,100000,100000,100001,40))
        assertEquals("offline",CommunicationHealth.state(false,true,false,0,0,100001,40))
    }
}
