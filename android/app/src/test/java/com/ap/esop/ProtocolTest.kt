package com.ap.esop

import com.google.gson.JsonParser
import org.junit.Assert.*
import org.junit.Test

class ProtocolTest {
    @Test fun canonicalUnicodeAndNumbersMatchPythonConvention(){
        val value=JsonParser.parseString("{\"z\":20.0,\"中\":\"<>&\u2028\",\"a\":[1,true,null]}")
        assertEquals("{\"a\":[1,true,null],\"z\":20.0,\"中\":\"<>&\u2028\"}",Wire.canonical(value))
    }
    @Test fun rejectExternalIds(){for(id in listOf("../escape","file?a=1","https://other"))try{Wire.id(id);fail("unsafe ID")}catch(e:IllegalArgumentException){}}
}
