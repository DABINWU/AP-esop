package com.ap.esop

import android.content.Context
import android.graphics.*
import android.view.*
import kotlin.math.max
import kotlin.math.min

class TouchImageView(context:Context):View(context) {
    private var bitmap:Bitmap?=null
    private var zoom=1f
    private var panX=0f
    private var panY=0f
    private var lastX=0f
    private var lastY=0f
    private var drawn:(()->Unit)?=null
    var onZoom:()->Unit={}
    private val paint=Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val scale=ScaleGestureDetector(context,object:ScaleGestureDetector.SimpleOnScaleGestureListener(){
        override fun onScaleBegin(detector:ScaleGestureDetector):Boolean {onZoom();return true}
        override fun onScale(detector:ScaleGestureDetector):Boolean {zoom=(zoom*detector.scaleFactor).coerceIn(1f,5f);invalidate();return true}
    })
    private val gestures=GestureDetector(context,object:GestureDetector.SimpleOnGestureListener(){
        override fun onDown(e:android.view.MotionEvent)=true
        override fun onDoubleTap(e:android.view.MotionEvent):Boolean {onZoom();resetZoom();return true}
    })
    fun display(value:Bitmap,ready:()->Unit) {
        val old=bitmap;bitmap=value;drawn=ready;resetZoom()
        if(old!==value)old?.recycle()
    }
    fun resetZoom(){zoom=1f;panX=0f;panY=0f;invalidate()}
    fun clear(){val old=bitmap;bitmap=null;drawn=null;old?.recycle();invalidate()}
    override fun onDraw(canvas:Canvas) {
        super.onDraw(canvas);canvas.drawColor(Color.WHITE)
        val image=bitmap ?: return
        val fit=min(width.toFloat()/image.width,height.toFloat()/image.height)
        val w=image.width*fit*zoom;val h=image.height*fit*zoom
        panX=panX.coerceIn(-max(0f,(w-width)/2),max(0f,(w-width)/2))
        panY=panY.coerceIn(-max(0f,(h-height)/2),max(0f,(h-height)/2))
        canvas.drawBitmap(image,null,RectF((width-w)/2+panX,(height-h)/2+panY,(width+w)/2+panX,(height+h)/2+panY),paint)
        drawn?.let { drawn=null;post(it) }
    }
    override fun onTouchEvent(event:MotionEvent):Boolean {
        scale.onTouchEvent(event);gestures.onTouchEvent(event)
        when(event.actionMasked){
            MotionEvent.ACTION_DOWN->{lastX=event.x;lastY=event.y}
            MotionEvent.ACTION_MOVE->{if(event.pointerCount==1 && !scale.isInProgress && zoom>1){panX+=event.x-lastX;panY+=event.y-lastY;invalidate()};lastX=event.x;lastY=event.y}
            MotionEvent.ACTION_UP->performClick()
        }
        return true
    }
    override fun performClick():Boolean {super.performClick();return true}
}
