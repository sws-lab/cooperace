// This file is part of the SV-Benchmarks collection of verification tasks:
// https://gitlab.com/sosy-lab/benchmarking/sv-benchmarks
//
// SPDX-FileCopyrightText: 2021 F. Schuessele <schuessf@informatik.uni-freiburg.de>
// SPDX-FileCopyrightText: 2021 D. Klumpp <klumpp@informatik.uni-freiburg.de>
//
// SPDX-License-Identifier: LicenseRef-BSD-3-Clause-Attribution-Vandikas

typedef unsigned long int pthread_t;

union pthread_attr_t
{
  char __size[36];
  long int __align;
};
typedef union pthread_attr_t pthread_attr_t;

extern void __assert_fail(const char *__assertion, const char *__file,
      unsigned int __line, const char *__function)
     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__noreturn__));
void reach_error() { __assert_fail("0", "bench-exp3x3-opt.wvr.c", 21, __extension__ __PRETTY_FUNCTION__); }
extern int pthread_create (pthread_t *__restrict __newthread,
      const pthread_attr_t *__restrict __attr,
      void *(*__start_routine) (void *),
      void *__restrict __arg) __attribute__ ((__nothrow__)) __attribute__ ((__nonnull__ (1, 3)));
extern int pthread_join (pthread_t __th, void **__thread_return);

extern unsigned int   __VERIFIER_nondet_uint(void);
extern _Bool __VERIFIER_nondet_bool(void);
extern void  __VERIFIER_atomic_begin();
extern void  __VERIFIER_atomic_end();

extern void abort(void);
void assume_abort_if_not(int cond) {
  if(!cond) {abort();}
}

unsigned int x1, x2, x3, x4, x5, x6, n;

void* thread1(void* _argptr) {
  while (x1 < n) {
    x1 = x1 + x1;
  }

  return 0;
}

void* thread2(void* _argptr) {
  while (x2 < n) {
    x2 = x2 + x2;
  }

  return 0;
}

void* thread3(void* _argptr) {
  while (x3 < n) {
    x3 = x3 + x3;
  }

  return 0;
}

void* thread4(void* _argptr) {
  while (x4 < n) {
    x4 = x4 + x4;
  }

  return 0;
}

void* thread5(void* _argptr) {
  while (x5 < n) {
    x5 = x5 + x5;
  }

  return 0;
}

void* thread6(void* _argptr) {
  while (x6 < n) {
    x6 = x6 + x6;
  }

  return 0;
}

int main() {
  pthread_t t1, t2, t3, t4, t5, t6;

  // initialize global variables
  x1 = __VERIFIER_nondet_uint();
  x2 = __VERIFIER_nondet_uint();
  x3 = __VERIFIER_nondet_uint();
  x4 = __VERIFIER_nondet_uint();
  x5 = __VERIFIER_nondet_uint();
  x6 = __VERIFIER_nondet_uint();
  n  = __VERIFIER_nondet_uint();

  // main method
  assume_abort_if_not( x1 == x2 && x3 == x4 && x5 == x6 );

  pthread_create(&t1, 0, thread1, 0);
  pthread_create(&t2, 0, thread2, 0);
  pthread_create(&t3, 0, thread3, 0);
  pthread_create(&t4, 0, thread4, 0);
  pthread_create(&t5, 0, thread5, 0);
  pthread_create(&t6, 0, thread6, 0);
  pthread_join(t1, 0);
  pthread_join(t2, 0);
  pthread_join(t3, 0);
  pthread_join(t4, 0);
  pthread_join(t5, 0);
  pthread_join(t6, 0);

  assume_abort_if_not( ( x1 >= n ) && ( x2 >= n ) && !( x1 == x2 ) && ( x3 >= n ) && ( x4 >= n ) && !( x3 == x4 ) && ( x5 >= n ) && ( x6 >= n ) && !( x5 == x6 ) );
  reach_error();

  return 0;
}
