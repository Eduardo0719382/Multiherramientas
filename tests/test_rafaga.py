import os, tempfile, threading, unittest, subprocess, json
import cv2
import numpy as np
from PIL import Image
from Rafaga import compose, prepare_photos, validate, export_burst, DEFAULT_SOUND

class BurstTest(unittest.TestCase):
 def test_collage_covers_every_pixel(self):
  with tempfile.TemporaryDirectory(dir='.') as tmp:
   photo=os.path.join(tmp,'transparente.png')
   Image.new('RGBA',(70,110),(255,0,0,100)).save(photo)
   for w,h in [(180,320),(320,180),(201,201)]:
    for n in [1,2,3,5,6,7,11,17]:
     with self.subTest(size=(w,h),count=n):
      settings=dict(mode='Collage acumulado',size=10)
      photos=prepare_photos([photo]*n,w,h,settings)
      coverage=np.zeros((h,w),dtype=np.uint16)
      for image,position in photos:
       x,y=position
       coverage[y:y+image.height,x:x+image.width] += np.asarray(image.getchannel('A')).astype(np.uint16)
      self.assertTrue(np.all(np.abs(coverage.astype(int)-255) <= 2), "Cobertura complementaria, sin huecos ni superposición")
      if n > 2:
       self.assertTrue(any(np.any((np.asarray(im)[:,:,3]>0)&(np.asarray(im)[:,:,3]<255)) for im,_ in photos))
      settings.update(start=0, interval=.1, hold=1, flash=False)
      black=compose(np.zeros((h,w,3),np.uint8),n*.1,photos,settings)
      white=compose(np.full((h,w,3),255,np.uint8),n*.1,photos,settings)
      np.testing.assert_array_equal(black,white)

 def test_zoom_starts_at_burst_and_can_be_disabled(self):
  yy,xx=np.indices((120,160))
  pattern=np.stack((xx%256, yy%256, (xx+yy)%256),axis=2).astype(np.uint8)
  photo=Image.fromarray(pattern).convert('RGBA')
  settings=dict(start=1,interval=.15,hold=2,mode='Collage acumulado',flash=False,zoom=True)
  background=np.full((120,160,3),30,np.uint8)
  photos=[(photo,(0,0))]
  np.testing.assert_array_equal(compose(background,.9,photos,settings),background)
  np.testing.assert_array_equal(compose(background,1,photos,settings),compose(background,1,photos,dict(settings,zoom=False)))
  self.assertFalse(np.array_equal(compose(background,1.3,photos,settings),compose(background,1.3,photos,dict(settings,zoom=False))))
  np.testing.assert_array_equal(compose(background,1.5,photos,settings),compose(background,2.5,photos,settings))
  np.testing.assert_array_equal(compose(background,3,photos,settings),background)

 def test_export_and_timing(self):
  with tempfile.TemporaryDirectory(dir='.') as tmp:
   video=os.path.join(tmp,'fondo.mp4'); photo=os.path.join(tmp,'foto.png'); output=os.path.join(tmp,'salida.mp4')
   Image.new('RGB',(80,80),'red').save(photo)
   writer=cv2.VideoWriter(video,cv2.VideoWriter_fourcc(*'mp4v'),25,(160,120))
   for _ in range(50): writer.write(np.zeros((120,160,3),dtype=np.uint8))
   writer.release()
   s=dict(start=.4, interval=.2, hold=.4, size=90, volume=100, mode='Collage acumulado',flash=False,keep_audio=True)
   photos=prepare_photos([photo,photo],160,120,s)
   frame=np.zeros((120,160,3),dtype=np.uint8)
   self.assertFalse(compose(frame,0,photos,s).any())
   self.assertGreater(compose(frame,.45,photos,s).sum(),0)
   self.assertGreater(compose(frame,.65,photos,s).sum(),compose(frame,.45,photos,s).sum())
   self.assertFalse(compose(frame,1.1,photos,s).any())
   export_burst(video,[photo,photo],DEFAULT_SOUND,output,s,threading.Event(),lambda *a:None)
   info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',output]))
   self.assertEqual({x['codec_type'] for x in info['streams']},{'video','audio'})
   self.assertAlmostEqual(float(info['format']['duration']),2,places=1)
   cap=cv2.VideoCapture(output)
   for t,visible in [(0,False),(.48,True),(1.2,False)]:
    cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,f=cap.read();self.assertTrue(ok);self.assertEqual(f.mean()>5,visible)
   cap.release()
   with self.assertRaises(ValueError):validate(dict(s,start=1.8),2,2)
   with self.assertRaises(ValueError):export_burst(video,[photo],DEFAULT_SOUND,video,s,threading.Event(),lambda *a:None)
   cancel=threading.Event();cancel.set()
   with self.assertRaises(InterruptedError):export_burst(video,[photo],DEFAULT_SOUND,output,s,cancel,lambda *a:None)
   self.assertTrue(os.path.isfile(output))
   # Background audio branch.
   audio_video=os.path.join(tmp,'con_audio.mp4')
   subprocess.run(['ffmpeg','-y','-v','error','-i',video,'-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','copy','-c:a','aac','-shortest',audio_video],check=True)
   export_burst(audio_video,[photo],DEFAULT_SOUND,output,s,threading.Event(),lambda *a:None)

if __name__=='__main__': unittest.main()
