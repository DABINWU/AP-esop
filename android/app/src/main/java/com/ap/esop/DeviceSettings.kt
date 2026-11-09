package com.ap.esop

import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.net.URI
import java.security.KeyStore
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.PBEKeySpec

data class DeviceConfig(val relayUrl:String,val deviceId:String,val deviceKey:String,val installId:String,
    val pinSalt:String,val pinHash:String,val pinAlgorithm:String,val bootEnabled:Boolean=false)

class DeviceSettings(context:Context) {
    private val prefs=context.getSharedPreferences("protected_device",Context.MODE_PRIVATE)
    private val display=context.getSharedPreferences("device_display",Context.MODE_PRIVATE)
    fun deviceName(id:String)=display.getString("name_$id",null)?.takeIf {it.isNotBlank()} ?: id
    fun rememberDeviceName(id:String,name:String) {if(display.getString("name_$id",null)!=name)check(display.edit().putString("name_$id",name.take(120)).commit())}
    private val alias="AP_ESOP_CONFIG_V1"
    private fun key():SecretKey {
        val store=KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias,null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(alias,KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    fun load():DeviceConfig? {
        val encoded=prefs.getString("config",null) ?: return null
        val bytes=Base64.decode(encoded,Base64.NO_WRAP)
        val cipher=Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE,key(),GCMParameterSpec(128,bytes.copyOfRange(0,12)))
        return Wire.gson.fromJson(String(cipher.doFinal(bytes.copyOfRange(12,bytes.size)),Charsets.UTF_8),DeviceConfig::class.java)
    }
    fun save(config:DeviceConfig) {
        val cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key())
        val bytes=cipher.iv+cipher.doFinal(Wire.gson.toJson(config).toByteArray(Charsets.UTF_8))
        check(prefs.edit().putString("config",Base64.encodeToString(bytes,Base64.NO_WRAP)).commit()) { "设备配置保存失败" }
    }
    fun create(url:String,id:String,accessKey:String,pin:String,old:DeviceConfig?,boot:Boolean):DeviceConfig {
        val endpoint=URI(url.trim().trimEnd('/'))
        require(endpoint.scheme in setOf("http","https") && endpoint.host!=null && endpoint.rawUserInfo==null && endpoint.rawQuery==null && endpoint.rawFragment==null && endpoint.path.orEmpty().isEmpty()) { "中转地址须为http(s)://主机:端口，不含路径或账号" }
        require(Regex("[A-Za-z0-9_-]{1,64}").matches(id.trim())) { "device_id仅支持字母、数字、下划线、短横线" }
        val secret=accessKey.ifBlank { old?.deviceKey.orEmpty() }
        require(secret.length>=24 && secret.all { it.code in 33..126 }) { "终端访问密钥至少24位ASCII，不含空格" }
        require(old==null || old.deviceId==id.trim()) { "本安装编号不可重置；更换设备请在中心登记新编号" }
        val algorithm=if(Build.VERSION.SDK_INT>=26) "PBKDF2WithHmacSHA256" else "PBKDF2WithHmacSHA1"
        val salt=if(pin.isBlank() && old!=null) old.pinSalt else Base64.encodeToString(ByteArray(16).also { SecureRandom().nextBytes(it) },Base64.NO_WRAP)
        val hash=if(pin.isBlank() && old!=null) old.pinHash else {
            require(pin.length>=6) { "管理员设置密码至少6位" };pinDigest(pin,salt,algorithm)
        }
        return DeviceConfig(endpoint.toString(),id.trim(),secret,old?.installId ?: UUID.randomUUID().toString(),salt,hash,if(pin.isBlank()&&old!=null)old.pinAlgorithm else algorithm,boot)
    }
    private fun pinDigest(pin:String,salt:String,algorithm:String)=Base64.encodeToString(SecretKeyFactory.getInstance(algorithm).generateSecret(PBEKeySpec(pin.toCharArray(),Base64.decode(salt,Base64.NO_WRAP),120000,256)).encoded,Base64.NO_WRAP)
    fun verifyPin(pin:String,config:DeviceConfig):Boolean {
        if(System.currentTimeMillis()<prefs.getLong("locked_until",0))return false
        val valid=MessageDigest.isEqual(pinDigest(pin,config.pinSalt,config.pinAlgorithm).toByteArray(),config.pinHash.toByteArray())
        val attempts=if(valid)0 else prefs.getInt("attempts",0)+1
        prefs.edit().putInt("attempts",if(attempts>=5)0 else attempts).putLong("locked_until",if(attempts>=5)System.currentTimeMillis()+60000 else 0).apply()
        return valid
    }
}
